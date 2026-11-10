"""Strategy definitions that people or the AI designer can create, and how they become trades."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import replace
from datetime import date
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field, model_validator

from algoverse.evaluation.config import Weight
from algoverse.evaluation.context import MarketContext
from algoverse.risk.config import ExitRule
from algoverse.strategies.base import BUY, SELL, Candidate, ExpiryView, make_leg

PROFILE_PREFIX = "custom:"
# Custom strategies trade on their entry rules. The score only reports structure quality.
NEUTRAL_WEIGHTS = {
    "spread_quality": 3,
    "leg_oi": 2,
    "ev_ratio": 3,
    "reward_risk": 2,
    "cost_efficiency": 3,
    "pop": 2,
}
NO_CONTRACTS = "no expiry or strikes match the rules"


class LegSpec(BaseModel):
    action: Literal["buy", "sell"]
    option_type: Literal["CE", "PE"]
    ratio: int = Field(default=1, ge=1, le=4)
    # delta: absolute delta to target. atm_offset: listed strikes above (+) or below (-) ATM.
    select: Literal["delta", "atm_offset"]
    value: float
    # 0 is the entry expiry, 1 the next one after it, and so on.
    expiry: int = Field(default=0, ge=0, le=2)

    @model_validator(mode="after")
    def _check_value(self) -> LegSpec:
        if self.select == "delta" and not 0.05 <= self.value <= 0.95:
            raise ValueError("delta must be between 0.05 and 0.95")
        if self.select == "atm_offset" and (self.value != int(self.value) or abs(self.value) > 20):
            raise ValueError("atm_offset must be a whole number between -20 and 20")
        return self


class EntryRules(BaseModel):
    # Empty means any trend.
    trend: list[Literal["up", "down", "range"]] = Field(default_factory=list)
    iv_rank_min: float | None = Field(default=None, ge=0, le=100)
    iv_rank_max: float | None = Field(default=None, ge=0, le=100)
    dte_min: int = Field(default=3, ge=1, le=45)
    dte_max: int = Field(default=30, ge=1, le=45)

    @model_validator(mode="after")
    def _check_ranges(self) -> EntryRules:
        if self.dte_min > self.dte_max:
            raise ValueError("dte_min must not exceed dte_max")
        if (
            self.iv_rank_min is not None
            and self.iv_rank_max is not None
            and self.iv_rank_min > self.iv_rank_max
        ):
            raise ValueError("iv_rank_min must not exceed iv_rank_max")
        return self


class ExitRules(BaseModel):
    # Fractions of the premium paid or received.
    target_frac: float = Field(default=0.5, ge=0.05, le=5)
    stop_frac: float = Field(default=0.5, ge=0.05, le=5)
    # Calendar days before the entry expiry for the 15:00 time exit.
    exit_dte: int = Field(default=1, ge=0, le=10)


class StrategySpec(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]{1,48}$")
    name: str = Field(min_length=1, max_length=60)
    rationale: str = Field(default="", max_length=600)
    source: Literal["template", "user", "ai"] = "user"
    bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    # Empty means every configured underlying.
    symbols: list[str] = Field(default_factory=list, max_length=10)
    legs: list[LegSpec] = Field(min_length=1, max_length=4)
    entry: EntryRules = Field(default_factory=EntryRules)
    exits: ExitRules = Field(default_factory=ExitRules)
    created_at: str | None = None

    @model_validator(mode="after")
    def _check_exit_window(self) -> StrategySpec:
        if self.entry.dte_min <= self.exits.exit_dte:
            raise ValueError("dte_min must be greater than exit_dte, or no trade can be entered")
        return self


def signature(spec: StrategySpec) -> str:
    """Identifies the trading rules, ignoring the name and description."""
    rules = spec.model_dump(include={"symbols", "legs", "entry", "exits", "bias"})
    rules["legs"] = sorted(rules["legs"], key=lambda leg: json.dumps(leg, sort_keys=True))
    rules["symbols"] = sorted(rules["symbols"])
    return hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest()[:12]


def make_id(name: str, spec_signature: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:32] or "strategy"
    return f"{slug}_{spec_signature[:6]}"


def profile_key(spec: StrategySpec) -> str:
    return PROFILE_PREFIX + spec.id


def has_undefined_risk(spec: StrategySpec) -> bool:
    """True when more calls or more puts are sold than bought."""
    net = Counter()
    for leg in spec.legs:
        net[leg.option_type] += (1 if leg.action == "buy" else -1) * leg.ratio
    return net["CE"] < 0 or net["PE"] < 0


def describe_legs(spec: StrategySpec) -> str:
    parts = []
    for leg in spec.legs:
        sign = "+" if leg.action == "buy" else "-"
        pick = (
            f"delta {leg.value:g}"
            if leg.select == "delta"
            else f"ATM{int(leg.value):+d}"
            if leg.value
            else "ATM"
        )
        later = f" expiry+{leg.expiry}" if leg.expiry else ""
        parts.append(f"{sign}{leg.ratio} {leg.option_type} {pick}{later}")
    return ", ".join(parts)


def build_candidate(
    spec: StrategySpec, symbol: str, views: list[ExpiryView], today: date
) -> Candidate | None:
    """Pick contracts for the nearest expiry inside the entry window, or None if none fit."""
    base = next(
        (
            index
            for index, view in enumerate(views)
            if spec.entry.dte_min <= (view.expiry - today).days <= spec.entry.dte_max
        ),
        None,
    )
    if base is None:
        return None
    legs, used = [], set()
    for leg in spec.legs:
        if base + leg.expiry >= len(views):
            return None
        view = views[base + leg.expiry]
        if leg.select == "delta":
            row = view.by_delta(leg.option_type, leg.value)
        else:
            atm = view.atm(leg.option_type)
            row = None if atm is None else view.offset(atm, int(leg.value))
        if row is None or row["instrument_key"] in used:
            return None
        used.add(row["instrument_key"])
        legs.append(make_leg(row, BUY if leg.action == "buy" else SELL, leg.ratio))
    return Candidate(symbol, spec.id, profile_key(spec), spec.bias, tuple(legs))


def entry_failures(spec: StrategySpec, ctx: MarketContext) -> list[str]:
    rules, reasons = spec.entry, []
    if rules.trend:
        trend = ctx.regime.trend if ctx.regime else None
        if trend not in rules.trend:
            reasons.append(f"entry rule: trend is {trend or 'unknown'}")
    if rules.iv_rank_min is not None or rules.iv_rank_max is not None:
        ranking = ctx.iv.ranking
        if ranking is None:
            reasons.append("entry rule: IV rank is not available yet")
        elif rules.iv_rank_min is not None and ranking.rank < rules.iv_rank_min:
            reasons.append("entry rule: IV rank is below the minimum")
        elif rules.iv_rank_max is not None and ranking.rank > rules.iv_rank_max:
            reasons.append("entry rule: IV rank is above the maximum")
    return reasons


class CustomSet:
    """Feeds custom strategies into the normal idea pipeline."""

    def __init__(self, specs: list[StrategySpec], only: bool = True):
        self.specs = specs
        self.only = only
        self.no_contracts: Counter = Counter()

    def candidates(
        self, ctx: MarketContext, chain: pd.DataFrame
    ) -> list[tuple[Candidate, list[str]]]:
        live = chain[chain["T"] > 0]
        views = [ExpiryView(group) for _, group in live.groupby("expiry", sort=True)]
        found = []
        for spec in self.specs:
            if spec.symbols and ctx.symbol not in spec.symbols:
                continue
            candidate = build_candidate(spec, ctx.symbol, views, ctx.now.date())
            if candidate is None:
                self.no_contracts[spec.id] += 1
            else:
                found.append((candidate, entry_failures(spec, ctx)))
        return found


def apply_specs(services, specs: list[StrategySpec]):
    """Return services whose scoring and exit settings know about these strategies."""
    scoring = services.scoring.model_copy(deep=True)
    risk = services.risk.model_copy(deep=True)
    for spec in specs:
        key = profile_key(spec)
        scoring.profiles[key] = {name: Weight(w=w) for name, w in NEUTRAL_WEIGHTS.items()}
        risk.exits[key] = ExitRule(**spec.exits.model_dump())
        if has_undefined_risk(spec):
            scoring.filters.block_on_high_events.append(key)
    risk.paper.max_new_per_cycle = max(len(services.backtest.symbols), 1)
    return replace(services, scoring=scoring, risk=risk)

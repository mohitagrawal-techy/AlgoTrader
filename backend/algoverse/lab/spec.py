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
from algoverse.lab.technical import Frames, TechRule, rule_failure, rule_text
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
SignalName = Literal[
    "iv_rank",
    "vix_rank",
    "vix_change_pct",
    "iv_minus_rv",
    "term_slope",
    "pcr",
    "skew",
    "oi_buildup",
    "spot_vs_max_pain_pct",
    "adx",
    "gap_pct",
    "range_pct",
]
SIGNALS = {
    "iv_rank": "IV rank (0 to 100)",
    "vix_rank": "India VIX rank (0 to 100)",
    "vix_change_pct": "India VIX change, %",
    "iv_minus_rv": "ATM IV minus realised volatility, points",
    "term_slope": "IV term structure slope",
    "pcr": "Put-call ratio",
    "skew": "25-delta skew, points",
    "oi_buildup": "OI build-up (-1 bearish to +1 bullish)",
    "spot_vs_max_pain_pct": "Spot vs max pain, %",
    "adx": "ADX trend strength",
    "gap_pct": "Latest session's opening gap, %",
    "range_pct": "Latest session's range, %",
}
SIMPLE_MAX_FILTERS = 1
MODERATE_MAX_FILTERS = 2


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


class Condition(BaseModel):
    signal: SignalName
    op: Literal["above", "below"]
    value: float


class EntryRules(BaseModel):
    # Empty means any trend.
    trend: list[Literal["up", "down", "range"]] = Field(default_factory=list)
    iv_rank_min: float | None = Field(default=None, ge=0, le=100)
    iv_rank_max: float | None = Field(default=None, ge=0, le=100)
    dte_min: int = Field(default=3, ge=1, le=45)
    dte_max: int = Field(default=30, ge=1, le=45)
    # Every condition must hold. Several together make a more selective strategy.
    conditions: list[Condition] = Field(default_factory=list, max_length=6)
    # Indicator, candle-pattern and price-level rules on the underlying, each on its own
    # timeframe. Every rule must hold too.
    rules: list[TechRule] = Field(default_factory=list, max_length=8)

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
    source: Literal["template", "user", "ai", "ladder"] = "user"
    bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    # Empty means every configured underlying.
    symbols: list[str] = Field(default_factory=list, max_length=10)
    legs: list[LegSpec] = Field(min_length=1, max_length=4)
    entry: EntryRules = Field(default_factory=EntryRules)
    exits: ExitRules = Field(default_factory=ExitRules)
    created_at: str | None = None
    # Set for strategies built by the ladder: where it came from and what changed.
    parent_id: str | None = None
    rung: int = Field(default=1, ge=1, le=20)
    step: str | None = Field(default=None, max_length=120)

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
    if not rules["entry"]["rules"]:
        # Strategies saved before technical rules existed keep their results.
        del rules["entry"]["rules"]
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


def _filter_count(spec: StrategySpec) -> int:
    entry = spec.entry
    has_iv_rule = entry.iv_rank_min is not None or entry.iv_rank_max is not None
    return int(bool(entry.trend)) + int(has_iv_rule) + len(entry.conditions) + len(entry.rules)


def complexity_level(spec: StrategySpec) -> str:
    """Simple, moderate or complex, from the legs and entry conditions it uses."""
    filters = _filter_count(spec)
    if len(spec.legs) >= 3 or filters > MODERATE_MAX_FILTERS:
        return "complex"
    if len(spec.legs) == 2 or filters > SIMPLE_MAX_FILTERS:
        return "moderate"
    return "simple"


def describe_entry(spec: StrategySpec) -> str:
    entry = spec.entry
    parts = [f"{entry.dte_min}-{entry.dte_max} days to expiry"]
    if entry.trend:
        parts.append(f"trend {'/'.join(entry.trend)}")
    if entry.iv_rank_min is not None:
        parts.append(f"IV rank at least {entry.iv_rank_min:g}")
    if entry.iv_rank_max is not None:
        parts.append(f"IV rank at most {entry.iv_rank_max:g}")
    parts += [f"{SIGNALS[c.signal]} {c.op} {c.value:g}" for c in entry.conditions]
    parts += [rule_text(rule) for rule in entry.rules]
    return ", ".join(parts)


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


def signal_value(name: str, ctx: MarketContext, front_expiry: date | None) -> float | None:
    """A market reading known at decision time, or None when it is not available yet."""
    regime = ctx.regime
    stats = ctx.by_expiry.get(front_expiry) if front_expiry else None
    if name == "iv_rank":
        return ctx.iv.ranking.rank if ctx.iv.ranking else None
    if name == "vix_rank":
        return ctx.vix_ranking.rank if ctx.vix_ranking else None
    if name == "vix_change_pct":
        return ctx.vix_change_pct
    if name == "term_slope":
        return ctx.term_slope
    if name in ("adx", "gap_pct", "range_pct"):
        return getattr(regime, name) if regime else None
    if stats is None:
        return None
    if name == "iv_minus_rv":
        known = stats.atm_iv is not None and ctx.realised_vol is not None
        return (stats.atm_iv - ctx.realised_vol) * 100 if known else None
    if name == "pcr":
        return stats.pcr
    if name == "skew":
        return None if stats.skew is None else stats.skew * 100
    if name == "oi_buildup":
        return stats.buildup
    if name == "spot_vs_max_pain_pct":
        return (ctx.spot / stats.max_pain - 1) * 100 if stats.max_pain else None
    return None


def entry_failures(
    spec: StrategySpec, ctx: MarketContext, front_expiry: date | None = None
) -> list[str]:
    rules, reasons = spec.entry, []
    if rules.rules:
        frames = Frames(ctx.bars)
        for rule in rules.rules:
            failure = rule_failure(rule, frames)
            if failure:
                reasons.append(failure)
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
    for condition in rules.conditions:
        value = signal_value(condition.signal, ctx, front_expiry)
        label = SIGNALS[condition.signal]
        if value is None:
            reasons.append(f"entry rule: {label} is not available")
        elif condition.op == "above" and value <= condition.value:
            reasons.append(f"entry rule: {label} is not above the level")
        elif condition.op == "below" and value >= condition.value:
            reasons.append(f"entry rule: {label} is not below the level")
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
                found.append((candidate, entry_failures(spec, ctx, candidate.front_expiry)))
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

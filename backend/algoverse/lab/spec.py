"""Strategy definitions that people or the AI designer can create, and how they become trades."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, time
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field, model_validator

from algoverse.evaluation.config import Weight
from algoverse.evaluation.context import MarketContext
from algoverse.lab.technical import Frames, TechRule, needs_minutes, rule_failure, rule_text
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
DecideEvery = Literal["day", "5m", "15m", "1h"]
DECIDE_MINUTES = {"5m": 5, "15m": 15, "1h": 60}
SESSION_OPEN = time(9, 15)
SESSION_LAST = time(15, 25)
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
    # Intraday strategies: only enter between these times. None means any time.
    time_from: time | None = None
    time_to: time | None = None
    # At most this many trades a day. None means no limit.
    max_trades_per_day: int | None = Field(default=None, ge=1, le=50)
    # False: after a stop-out, no new trade for the rest of the day.
    reenter_after_stop: bool = True

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
        for moment in (self.time_from, self.time_to):
            if moment is not None and not SESSION_OPEN <= moment <= SESSION_LAST:
                raise ValueError("entry times must be between 09:15 and 15:25")
        if self.time_from and self.time_to and self.time_from >= self.time_to:
            raise ValueError("time_from must be before time_to")
        return self


ExitUnit = Literal["premium", "rupees", "points"]
# Allowed amounts per unit: a fraction of the premium, rupees per lot, option points per lot.
UNIT_RANGES = {"premium": (0.05, 5.0), "rupees": (1.0, 1_000_000.0), "points": (0.05, 10_000.0)}


def _check_amount(name: str, value: float | None, unit: str) -> None:
    if value is None:
        return
    low, high = UNIT_RANGES[unit]
    if not low <= value <= high:
        raise ValueError(f"{name} in {unit} must be between {low:g} and {high:g}")


class ExitRules(BaseModel):
    # Profit target and stop loss, each in its unit: "premium" is a fraction of the premium
    # paid or received (0.5 is half), "rupees" is rupees per lot, "points" is option points.
    target_frac: float = Field(default=0.5, gt=0)
    stop_frac: float = Field(default=0.5, gt=0)
    target_unit: ExitUnit = "premium"
    stop_unit: ExitUnit = "premium"
    # Calendar days before the entry expiry for the 15:00 time exit.
    exit_dte: int = Field(default=1, ge=0, le=10)
    # Intraday strategies: close by this time every day. None holds positions overnight.
    square_off: time | None = None
    # Trailing stop: once in profit, the stop follows the best P&L at this distance.
    trail: float | None = None
    trail_unit: ExitUnit = "premium"
    # Move-to-cost: once the gain reaches this, the stop moves up to the entry price.
    breakeven_at: float | None = None
    breakeven_unit: ExitUnit = "premium"
    # Exit when any of these indicator, pattern or price rules holds.
    rules: list[TechRule] = Field(default_factory=list, max_length=8)
    # Stop this strategy for the day once its P&L that day reaches either level, in rupees.
    daily_stop: float | None = Field(default=None, gt=0)
    daily_target: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _in_session(self) -> ExitRules:
        if self.square_off is not None and not SESSION_OPEN < self.square_off <= SESSION_LAST:
            raise ValueError("square_off must be between 09:15 and 15:25")
        _check_amount("target", self.target_frac, self.target_unit)
        _check_amount("stop", self.stop_frac, self.stop_unit)
        _check_amount("trail", self.trail, self.trail_unit)
        _check_amount("breakeven_at", self.breakeven_at, self.breakeven_unit)
        return self


class StrategySpec(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]{1,48}$")
    name: str = Field(min_length=1, max_length=60)
    rationale: str = Field(default="", max_length=600)
    source: Literal["template", "user", "ai", "ladder"] = "user"
    bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    # Empty means every configured underlying.
    symbols: list[str] = Field(default_factory=list, max_length=10)
    legs: list[LegSpec] = Field(min_length=1, max_length=4)
    # "day" decides once a day at the close. "5m", "15m" and "1h" decide through the session
    # in the intraday backtester, entering at the next decision time.
    decide_every: DecideEvery = "day"
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
        intraday_only = (
            self.entry.time_from or self.entry.time_to or self.exits.square_off
        ) is not None
        minute_rules = needs_minutes(self.entry.rules) or needs_minutes(self.exits.rules)
        if self.decide_every == "day" and (intraday_only or minute_rules):
            raise ValueError(
                "entry times, square-off and 5m/15m/1h rules need a decision every 5, 15 or 60 "
                "minutes"
            )
        return self


def signature(spec: StrategySpec) -> str:
    """Identifies the trading rules, ignoring the name and description."""
    rules = spec.model_dump(
        include={"symbols", "legs", "entry", "exits", "bias", "decide_every"}, mode="json"
    )
    rules["legs"] = sorted(rules["legs"], key=lambda leg: json.dumps(leg, sort_keys=True))
    rules["symbols"] = sorted(rules["symbols"])
    # Settings added later are left out at their defaults, so older strategies keep their
    # results.
    for section, key, default in LATER_SETTINGS:
        holder = rules if section is None else rules[section]
        if holder.get(key) == default:
            del holder[key]
    return hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest()[:12]


LATER_SETTINGS = (
    ("entry", "rules", []),
    ("entry", "time_from", None),
    ("entry", "time_to", None),
    ("exits", "square_off", None),
    ("exits", "target_unit", "premium"),
    ("exits", "stop_unit", "premium"),
    ("exits", "trail", None),
    ("exits", "trail_unit", "premium"),
    ("exits", "breakeven_at", None),
    ("exits", "breakeven_unit", "premium"),
    ("exits", "rules", []),
    ("exits", "daily_stop", None),
    ("exits", "daily_target", None),
    ("entry", "max_trades_per_day", None),
    ("entry", "reenter_after_stop", True),
    (None, "decide_every", "day"),
)


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
    if entry.time_from or entry.time_to:
        start = f"{entry.time_from:%H:%M}" if entry.time_from else "09:15"
        end = f"{entry.time_to:%H:%M}" if entry.time_to else "15:25"
        parts.append(f"between {start} and {end}")
    if spec.decide_every != "day":
        parts.append(f"checked every {spec.decide_every}")
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
    if (rules.time_from or rules.time_to or spec.exits.square_off) is not None:
        moment = ctx.now.time()
        if rules.time_from is not None and moment < rules.time_from:
            reasons.append(f"entry rule: before {rules.time_from:%H:%M}")
        if rules.time_to is not None and moment > rules.time_to:
            reasons.append(f"entry rule: after {rules.time_to:%H:%M}")
        if spec.exits.square_off is not None and moment >= spec.exits.square_off:
            reasons.append("entry rule: past the square-off time")
    if rules.rules:
        frames = Frames(ctx.bars, getattr(ctx, "intraday", None))
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


STOPPED_OUT = {"stop", "trailing_stop", "stop_at_cost"}


def _day(moment: str | None):
    return None if moment is None else datetime.fromisoformat(moment).date()


class CustomSet:
    """Feeds custom strategies into the normal idea pipeline, and enforces their exit rules
    and daily limits on the positions they opened."""

    def __init__(self, specs: list[StrategySpec], only: bool = True):
        self.specs = specs
        self.only = only
        self.no_contracts: Counter = Counter()
        # The broker whose positions the daily limits count. Set by the cycle.
        self.broker = None

    def _positions(self, spec: StrategySpec) -> list:
        if self.broker is None:
            return []
        statuses = ("pending", "open", "exiting", "closed")
        return [p for p in self.broker.ledger.positions(statuses) if p.strategy == spec.id]

    def _day_pnl(self, positions: list, today) -> float:
        total = 0.0
        for position in positions:
            if position.status == "closed" and _day(position.closed_at) == today:
                total += position.realized_pnl or 0.0
            elif position.status in ("open", "exiting"):
                total += position.unrealized_pnl - position.entry_costs
        return total

    def limit_failures(self, spec: StrategySpec, today) -> list[str]:
        """Why the strategy may not open another trade today."""
        entry, exits = spec.entry, spec.exits
        if (
            entry.max_trades_per_day is None
            and entry.reenter_after_stop
            and exits.daily_stop is None
            and exits.daily_target is None
        ):
            return []
        positions = self._positions(spec)
        reasons = []
        today_trades = [p for p in positions if _day(p.created_at) == today]
        if entry.max_trades_per_day is not None and len(today_trades) >= entry.max_trades_per_day:
            reasons.append("entry rule: the day's trade limit is reached")
        if not entry.reenter_after_stop and any(
            p.status == "closed" and _day(p.closed_at) == today and p.exit_reason in STOPPED_OUT
            for p in positions
        ):
            reasons.append("entry rule: no re-entry after a stop today")
        pnl = self._day_pnl(positions, today)
        if exits.daily_stop is not None and pnl <= -exits.daily_stop:
            reasons.append("entry rule: the day's loss limit is reached")
        if exits.daily_target is not None and pnl >= exits.daily_target:
            reasons.append("entry rule: the day's profit target is reached")
        return reasons

    def exit_requests(self, ctx: MarketContext) -> list[tuple[int, str]]:
        """Open positions of these strategies that their exit rules or daily limits close."""
        requests = []
        for spec in self.specs:
            exits = spec.exits
            if not (exits.rules or exits.daily_stop or exits.daily_target):
                continue
            positions = self._positions(spec)
            mine = [p for p in positions if p.status == "open" and p.symbol == ctx.symbol]
            if not mine:
                continue
            reason = None
            pnl = self._day_pnl(positions, ctx.now.date())
            if exits.daily_stop is not None and pnl <= -exits.daily_stop:
                reason = "daily_stop"
            elif exits.daily_target is not None and pnl >= exits.daily_target:
                reason = "daily_target"
            elif exits.rules:
                frames = Frames(ctx.bars, getattr(ctx, "intraday", None))
                if any(rule_failure(rule, frames) is None for rule in exits.rules):
                    reason = "exit_rule"
            if reason:
                requests += [(position.id, reason) for position in mine]
        return requests

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
                failures = entry_failures(spec, ctx, candidate.front_expiry)
                failures += self.limit_failures(spec, ctx.now.date())
                found.append((candidate, failures))
        return found


def apply_specs(services, specs: list[StrategySpec]):
    """Return services whose scoring and exit settings know about these strategies."""
    scoring = services.scoring.model_copy(deep=True)
    risk = services.risk.model_copy(deep=True)
    for spec in specs:
        key = profile_key(spec)
        scoring.profiles[key] = {name: Weight(w=w) for name, w in NEUTRAL_WEIGHTS.items()}
        risk.exits[key] = ExitRule(**spec.exits.model_dump(exclude={"rules"}))
        if has_undefined_risk(spec):
            scoring.filters.block_on_high_events.append(key)
    risk.paper.max_new_per_cycle = max(len(services.backtest.symbols), 1)
    return replace(services, scoring=scoring, risk=risk)

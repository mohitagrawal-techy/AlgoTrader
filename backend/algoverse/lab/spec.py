"""Strategy definitions that people or the AI designer can create, and how they become trades."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, model_validator

from algoverse.evaluation.config import Weight
from algoverse.evaluation.context import MarketContext
from algoverse.lab.technical import (
    INDICATORS,
    PATTERNS,
    BaseRule,
    CandleType,
    Frames,
    SignalRule,
    TechRule,
    Timeframe,
    last_minute_day,
    needs_minutes,
    rule_failure,
    rule_reading,
    rule_text,
    rules_failures,
    rules_text,
)
from algoverse.research.ml import probability as ml_probability
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
    "ml_prob_up",
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
    "ml_prob_up": "ML chance the index closes up next (0 to 1), walk-forward",
}
DecideEvery = Literal["day", "5m", "15m", "1h"]
DECIDE_MINUTES = {"5m": 5, "15m": 15, "1h": 60}
SESSION_OPEN = time(9, 15)
SESSION_LAST = time(15, 25)
# The first five-minute candle has finished by now, so a session's minutes should exist.
FIRST_CANDLE_DONE = time(9, 20)
SIMPLE_MAX_FILTERS = 1
MODERATE_MAX_FILTERS = 2


LegUnit = Literal["pct", "points"]
LEG_UNIT_RANGES = {"pct": (1.0, 500.0), "points": (0.05, 10_000.0)}


class LegSpec(BaseModel):
    action: Literal["buy", "sell"]
    option_type: Literal["CE", "PE"]
    ratio: int = Field(default=1, ge=1, le=4)
    # delta: absolute delta to target. atm_offset: listed strikes above (+) or below (-) ATM.
    # premium: the strike whose option price is nearest this many rupees.
    select: Literal["delta", "atm_offset", "premium"]
    value: float
    # 0 is the entry expiry, 1 the next one after it, and so on.
    expiry: int = Field(default=0, ge=0, le=2)
    # This leg's own stop loss and profit target, against its fill price: "pct" is a percent
    # of the premium, "points" option points. Hitting one closes only this leg. None: unused.
    stop: float | None = None
    stop_unit: LegUnit = "pct"
    target: float | None = None
    target_unit: LegUnit = "pct"

    @model_validator(mode="after")
    def _check_value(self) -> LegSpec:
        for name, amount, unit in (
            ("leg stop", self.stop, self.stop_unit),
            ("leg target", self.target, self.target_unit),
        ):
            if amount is not None:
                low, high = LEG_UNIT_RANGES[unit]
                if not low <= amount <= high:
                    raise ValueError(f"{name} in {unit} must be between {low:g} and {high:g}")
        if self.select == "delta" and not 0.05 <= self.value <= 0.95:
            raise ValueError("delta must be between 0.05 and 0.95")
        if self.select == "atm_offset" and (self.value != int(self.value) or abs(self.value) > 20):
            raise ValueError("atm_offset must be a whole number between -20 and 20")
        if self.select == "premium" and not 0.5 <= self.value <= 5000:
            raise ValueError("premium must be between 0.5 and 5000 rupees")
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
    # 0 allows entering on expiry day itself, for intraday strategies that square off.
    dte_min: int = Field(default=3, ge=0, le=45)
    dte_max: int = Field(default=30, ge=1, le=45)
    # Every condition must hold. Several together make a more selective strategy.
    conditions: list[Condition] = Field(default_factory=list, max_length=6)
    # Indicator, candle-pattern and price-level rules on the underlying, each on its own
    # timeframe. Every rule must hold too.
    rules: list[TechRule] = Field(default_factory=list, max_length=8)
    # At least one of these must hold as well, for setups that can start in more than one way.
    any_rules: list[TechRule] = Field(default_factory=list, max_length=8)
    # Intraday strategies: only enter between these times. None means any time.
    time_from: time | None = None
    time_to: time | None = None
    # At most this many trades a day. None means no limit.
    max_trades_per_day: int | None = Field(default=None, ge=1, le=50)
    # False: after a stop-out, no new trade for the rest of the day.
    reenter_after_stop: bool = True
    # False: after a profit target, no new trade for the rest of the day.
    reenter_after_target: bool = True
    # Wait and trade: once the rules hold, wait until the chosen contracts' premium moves this
    # far ("pct" percent or "points") in `wait_direction`, then enter. Intraday only; a wait
    # lapses at the end of the day. None enters at once.
    wait_move: float | None = None
    wait_unit: LegUnit = "pct"
    wait_direction: Literal["up", "down"] = "up"

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
        if self.wait_move is not None:
            low, high = LEG_UNIT_RANGES[self.wait_unit]
            if not low <= self.wait_move <= high:
                raise ValueError(f"wait in {self.wait_unit} must be between {low:g} and {high:g}")
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
    # Stepped trailing stop: with a step, the stop instead rises by `trail` for every full
    # `trail_step` of gain, from where it started ("every 10 points, raise it 2"). Same unit.
    trail_step: float | None = None
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
        _check_amount("trail_step", self.trail_step, self.trail_unit)
        if self.trail_step is not None and self.trail is None:
            raise ValueError("a trailing step needs the amount the stop moves by")
        _check_amount("breakeven_at", self.breakeven_at, self.breakeven_unit)
        return self


class Budget(BaseModel):
    """The account a strategy is tested as. Every limit the risk engine expresses as a share of
    capital applies to this capital instead of the whole account's."""

    capital: float = Field(default=100_000, ge=10_000, le=100_000_000)
    # What one trade may lose, as a percent of `capital`. Lots are sized from this.
    # The default, 5% of ₹1,00,000, is ₹5,000: what one trade could risk on the whole account.
    risk_per_trade_pct: float = Field(default=5.0, gt=0, le=25)
    # How many of this strategy's positions may be open at once.
    max_open: int = Field(default=5, ge=1, le=10)
    # "budget": spend up to `deploy_pct` of capital per trade, as many lots as that buys.
    # "risk": size so one trade risks at most `risk_per_trade_pct`, within the account's
    # Greek and stress limits.
    sizing: Literal["budget", "risk"] = "budget"
    deploy_pct: float = Field(default=100.0, ge=1, le=100)
    # "atr": scale each trade's budget by the underlying's typical daily range (14-day ATR as
    # a share of price) against its median over the past year: smaller when markets swing
    # more. The scale stays between vol_floor and vol_cap; a cap of 1 only ever sizes down.
    vol_sizing: Literal["off", "atr"] = "off"
    vol_floor: float = Field(default=0.5, ge=0.1, le=1)
    vol_cap: float = Field(default=1.0, ge=1, le=2)
    # Circuit breakers: after this many losing trades in a row, or a fall of this percent of
    # capital from the strategy's best closed P&L, open no new trade for `pause_sessions`
    # weekdays. Open positions run on to their own exits. None switches a breaker off.
    pause_after_losses: int | None = Field(default=None, ge=2, le=20)
    pause_drawdown_pct: float | None = Field(default=None, ge=1, le=100)
    pause_sessions: int = Field(default=5, ge=1, le=60)

    @property
    def risk_per_trade(self) -> float:
        return self.capital * self.risk_per_trade_pct / 100


# Budget settings added later, left out of the stored budget at their defaults so results
# tested before they existed still match.
LATER_BUDGET_SETTINGS = (
    ("vol_sizing", "off"),
    ("vol_floor", 0.5),
    ("vol_cap", 1.0),
    ("pause_after_losses", None),
    ("pause_drawdown_pct", None),
    ("pause_sessions", 5),
)


def budget_key(budget: Budget | dict) -> dict:
    """The budget as stored with a result, to tell whether the result still applies. Takes a
    budget or a stored budget dict, so either form compares equal."""
    data = dict(budget) if isinstance(budget, dict) else budget.model_dump()
    for key, default in LATER_BUDGET_SETTINGS:
        if key in data and data[key] == default:
            del data[key]
    return data


class IndicatorPick(BaseModel):
    """An indicator or candle pattern chosen for the strategy: its settings, the candles it is
    read on, and a name of the user's choosing. Rules in every case pick from these, and a rule
    reads them on their candles. It only shapes the editor: trades follow the rules alone."""

    name: str
    params: dict[str, float] = Field(default_factory=dict)
    label: str | None = Field(default=None, max_length=40)
    timeframe: Timeframe = "1d"
    candles: CandleType = "normal"

    @model_validator(mode="after")
    def _known(self) -> IndicatorPick:
        if self.name not in INDICATORS and self.name not in PATTERNS:
            raise ValueError(f"unknown indicator {self.name}")
        return self


class NamedSignal(BaseModel):
    """A named set of conditions joined by and/or, such as "Sell signal 1": the 15-minute
    opening range closed green and the 5-minute low broke its open. Entry and exit conditions
    read it as true or false."""

    name: str = Field(min_length=1, max_length=40)
    rules: list[BaseRule] = Field(min_length=1, max_length=8)


class EntryCase(BaseModel):
    """Another way into the same strategy: its own legs, indicator rules and, optionally, exit
    rules. The time window, expiry window, market filters, day limits, stop and target are the
    strategy's own."""

    name: str = Field(min_length=1, max_length=40)
    bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    legs: list[LegSpec] = Field(min_length=1, max_length=4)
    rules: list[TechRule] = Field(default_factory=list, max_length=8)
    any_rules: list[TechRule] = Field(default_factory=list, max_length=8)
    # This case's own exit rules. None uses the strategy's.
    exit_rules: list[TechRule] | None = Field(default=None, max_length=8)


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
    # More cases after the first (the legs and entry rules above). The first whose rules hold
    # trades; the day's trade limit counts them together.
    cases: list[EntryCase] = Field(default_factory=list, max_length=3)
    budget: Budget = Field(default_factory=Budget)
    # The indicators the strategy's rules choose from. Not part of the signature.
    indicators: list[IndicatorPick] = Field(default_factory=list, max_length=24)
    # Named signals the entry and exit conditions read as true or false. Their conditions are
    # copied into each condition that reads them, so the signature follows what is traded.
    signals: list[NamedSignal] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def _fill_signals(self) -> StrategySpec:
        found = {signal.name.strip().lower(): signal for signal in self.signals}
        if len(found) != len(self.signals):
            raise ValueError("two signals have the same name")
        lists = [
            self.entry.rules,
            self.entry.any_rules,
            self.exits.rules,
            *[case.rules for case in self.cases],
            *[case.any_rules for case in self.cases],
            *[case.exit_rules or [] for case in self.cases],
        ]
        for rules in lists:
            for rule in rules:
                if isinstance(rule, SignalRule):
                    signal = found.get(rule.name.strip().lower())
                    if signal is None:
                        raise ValueError(f"no signal is named {rule.name!r}")
                    rule.name = signal.name
                    rule.rules = [inner.model_copy(deep=True) for inner in signal.rules]
        return self

    @model_validator(mode="after")
    def _check_exit_window(self) -> StrategySpec:
        # An intraday strategy that squares off may enter on its time-exit day, before 15:00;
        # otherwise the time exit would close it as soon as it opened.
        same_day = self.decide_every != "day" and self.exits.square_off is not None
        if self.entry.dte_min < self.exits.exit_dte or (
            self.entry.dte_min == self.exits.exit_dte and not same_day
        ):
            raise ValueError("dte_min must be greater than exit_dte, or no trade can be entered")
        intraday_only = (
            self.entry.time_from or self.entry.time_to or self.exits.square_off
        ) is not None
        minute_rules = any(
            needs_minutes(rules)
            for rules in (
                self.entry.rules,
                self.entry.any_rules,
                self.exits.rules,
                *[case.rules for case in self.cases],
                *[case.any_rules for case in self.cases],
                *[case.exit_rules or [] for case in self.cases],
            )
        )
        if self.decide_every == "day" and (intraday_only or minute_rules):
            raise ValueError(
                "entry times, square-off and 5m/15m/1h rules need a decision every 5, 15 or 60 "
                "minutes"
            )
        if self.decide_every == "day" and self.entry.wait_move is not None:
            raise ValueError("wait and trade needs a decision every 5, 15 or 60 minutes")
        return self


def signature(spec: StrategySpec) -> str:
    """Identifies the trading rules, ignoring the name and description."""
    rules = spec.model_dump(
        include={"symbols", "legs", "entry", "exits", "bias", "decide_every", "cases"},
        mode="json",
    )
    for leg in rules["legs"] + [leg for case in rules["cases"] for leg in case["legs"]]:
        for key, default in LATER_LEG_SETTINGS:
            if leg.get(key) == default:
                del leg[key]
    rules["legs"] = sorted(rules["legs"], key=lambda leg: json.dumps(leg, sort_keys=True))
    for case in rules["cases"]:
        if case.get("exit_rules") is None:
            case.pop("exit_rules", None)
        for rule in case["rules"] + case["any_rules"] + (case.get("exit_rules") or []):
            if rule.get("candles") == "normal":
                del rule["candles"]
            if rule.get("join") == "and":
                del rule["join"]
    rules["symbols"] = sorted(rules["symbols"])
    # Settings added later are left out at their defaults, so older strategies keep their
    # results.
    for section, key, default in LATER_SETTINGS:
        holder = rules if section is None else rules[section]
        if holder.get(key) == default:
            del holder[key]
    for section, key in (("entry", "rules"), ("entry", "any_rules"), ("exits", "rules")):
        for rule in rules[section].get(key, []):
            if rule.get("candles") == "normal":
                del rule["candles"]
            if rule.get("join") == "and":
                del rule["join"]
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
    ("exits", "trail_step", None),
    ("exits", "breakeven_at", None),
    ("exits", "breakeven_unit", "premium"),
    ("exits", "rules", []),
    ("exits", "daily_stop", None),
    ("exits", "daily_target", None),
    ("entry", "max_trades_per_day", None),
    ("entry", "reenter_after_stop", True),
    ("entry", "any_rules", []),
    (None, "decide_every", "day"),
    (None, "cases", []),
    ("entry", "reenter_after_target", True),
    ("entry", "wait_move", None),
    ("entry", "wait_unit", "pct"),
    ("entry", "wait_direction", "up"),
)
LATER_LEG_SETTINGS = (
    ("stop", None),
    ("stop_unit", "pct"),
    ("target", None),
    ("target_unit", "pct"),
)


def make_id(name: str, spec_signature: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:32] or "strategy"
    return f"{slug}_{spec_signature[:6]}"


def profile_key(spec: StrategySpec) -> str:
    return PROFILE_PREFIX + spec.id


def variants(spec: StrategySpec) -> list[StrategySpec]:
    """The strategy's first case, then each further one as the same strategy (same id, limits,
    stop and target) with that case's legs, indicator rules and any exit rules of its own."""
    found = [spec.model_copy(update={"cases": []})]
    for case in spec.cases:
        entry = spec.entry.model_copy(update={"rules": case.rules, "any_rules": case.any_rules})
        exits = (
            spec.exits
            if case.exit_rules is None
            else spec.exits.model_copy(update={"rules": case.exit_rules})
        )
        found.append(
            spec.model_copy(
                update={
                    "legs": case.legs,
                    "entry": entry,
                    "exits": exits,
                    "bias": case.bias,
                    "cases": [],
                }
            )
        )
    return found


def has_undefined_risk(spec: StrategySpec) -> bool:
    """True when any case sells more calls or more puts than it buys."""
    for variant in variants(spec):
        net = Counter()
        for leg in variant.legs:
            net[leg.option_type] += (1 if leg.action == "buy" else -1) * leg.ratio
        if net["CE"] < 0 or net["PE"] < 0:
            return True
    return False


def _closeness(leg: LegSpec) -> float:
    """Higher is nearer the money, comparable between legs that pick strikes the same way."""
    if leg.select == "delta":
        return abs(leg.value)
    if leg.select == "premium":
        return leg.value
    return -abs(leg.value)


def _inner_sold(legs: list[LegSpec]) -> bool | None:
    """Whether the sold legs sit nearer the money than the bought ones, on every side that
    has both. None when no side can be compared."""
    verdicts = []
    for option_type in ("CE", "PE"):
        side = [leg for leg in legs if leg.option_type == option_type and leg.expiry == 0]
        sold = [leg for leg in side if leg.action == "sell"]
        bought = [leg for leg in side if leg.action == "buy"]
        if not sold or not bought or len({leg.select for leg in side}) > 1:
            continue
        verdicts.append(
            min(_closeness(leg) for leg in sold) > max(_closeness(leg) for leg in bought)
        )
    return None if not verdicts else all(verdicts)


def structure_problems(spec: StrategySpec) -> list[str]:
    """Ways the legs contradict the structure the name promises, such as an "iron condor"
    that sells the wings and buys the body, which is really a reverse iron condor."""
    name = spec.name.lower().replace("-", " ").replace("\u2011", " ")
    problems = []
    for variant in variants(spec):
        legs = list(variant.legs)
        actions = {leg.action for leg in legs}
        inner_sold = _inner_sold(legs)
        if "condor" in name and "reverse" not in name and inner_sold is False:
            problems.append(
                "named an iron condor, but it buys the legs nearer the money and sells the "
                "wings, which is a reverse iron condor"
            )
        if "reverse" in name and "condor" in name and inner_sold is True:
            problems.append("named a reverse iron condor, but it sells the legs nearer the money")
        if "credit spread" in name and inner_sold is False:
            problems.append("named a credit spread, but it buys the strike nearer the money")
        if "debit spread" in name and inner_sold is True:
            problems.append("named a debit spread, but it sells the strike nearer the money")
        if ("straddle" in name or "strangle" in name) and "short" in name and "buy" in actions:
            problems.append("named a short straddle or strangle, but it buys options")
        if ("straddle" in name or "strangle" in name) and "long" in name and "sell" in actions:
            problems.append("named a long straddle or strangle, but it sells options")
    return list(dict.fromkeys(problems))


def _filter_count(spec: StrategySpec) -> int:
    entry = spec.entry
    has_iv_rule = entry.iv_rank_min is not None or entry.iv_rank_max is not None
    return (
        int(bool(entry.trend))
        + int(has_iv_rule)
        + len(entry.conditions)
        + len(entry.rules)
        + int(bool(entry.any_rules))
    )


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
    if entry.rules:
        parts.append(rules_text(entry.rules))
    if entry.any_rules:
        parts.append("any of: " + " or ".join(rule_text(rule) for rule in entry.any_rules))
    if entry.time_from or entry.time_to:
        start = f"{entry.time_from:%H:%M}" if entry.time_from else "09:15"
        end = f"{entry.time_to:%H:%M}" if entry.time_to else "15:25"
        parts.append(f"between {start} and {end}")
    if spec.decide_every != "day":
        parts.append(f"checked every {spec.decide_every}")
    if entry.wait_move is not None:
        amount = (
            f"{entry.wait_move:g}%" if entry.wait_unit == "pct" else f"{entry.wait_move:g} points"
        )
        parts.append(f"then wait for the premium to move {entry.wait_direction} {amount}")
    text = ", ".join(parts)
    for case in spec.cases:
        rules = [rules_text(case.rules)] if case.rules else []
        if case.any_rules:
            rules.append("any of: " + " or ".join(rule_text(rule) for rule in case.any_rules))
        legs = describe_legs(spec.model_copy(update={"legs": case.legs, "cases": []}))
        text += f"; or {case.name}: {legs} when {', '.join(rules) or 'the shared rules hold'}"
    return text


def describe_legs(spec: StrategySpec) -> str:
    parts = []
    for leg in spec.legs:
        sign = "+" if leg.action == "buy" else "-"
        pick = (
            f"delta {leg.value:g}"
            if leg.select == "delta"
            else f"premium near {leg.value:g}"
            if leg.select == "premium"
            else f"ATM{int(leg.value):+d}"
            if leg.value
            else "ATM"
        )
        later = f" expiry+{leg.expiry}" if leg.expiry else ""
        levels = [
            f"{name} {amount:g}{'%' if unit == 'pct' else ' pts'}"
            for name, amount, unit in (
                ("SL", leg.stop, leg.stop_unit),
                ("TP", leg.target, leg.target_unit),
            )
            if amount is not None
        ]
        own = f" ({', '.join(levels)})" if levels else ""
        parts.append(f"{sign}{leg.ratio} {leg.option_type} {pick}{later}{own}")
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
        elif leg.select == "premium":
            row = view.by_premium(leg.option_type, leg.value)
        else:
            atm = view.atm(leg.option_type)
            row = None if atm is None else view.offset(atm, int(leg.value))
        if row is None or row["instrument_key"] in used:
            return None
        used.add(row["instrument_key"])
        legs.append(
            replace(
                make_leg(row, BUY if leg.action == "buy" else SELL, leg.ratio),
                stop=leg.stop,
                stop_unit=leg.stop_unit,
                target=leg.target,
                target_unit=leg.target_unit,
            )
        )
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
    if name == "ml_prob_up":
        # Saved walk-forward probabilities: each from a model that never saw that session.
        return ml_probability(ctx.symbol, ctx.now.date(), ctx.now.time() >= SESSION_LAST)
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


def quick_failures(
    spec: StrategySpec, now: datetime | None, frames: Callable[[], Frames]
) -> list[str]:
    """The entry checks that need no option chain: the time window and candle rules.

    They come first in `entry_failures`, so a strategy failing one is rejected for that reason
    whatever the chain shows. Intraday replays use this to skip decisions nothing can act on."""
    rules, reasons = spec.entry, []
    if (rules.time_from or rules.time_to or spec.exits.square_off) is not None:
        moment = now.time()
        if rules.time_from is not None and moment < rules.time_from:
            reasons.append(f"entry rule: before {rules.time_from:%H:%M}")
        if rules.time_to is not None and moment > rules.time_to:
            reasons.append(f"entry rule: after {rules.time_to:%H:%M}")
        if spec.exits.square_off is not None and moment >= spec.exits.square_off:
            reasons.append("entry rule: past the square-off time")
    if rules.rules or rules.any_rules:
        built = frames()
        # Minute rules must read this session's candles. Without any (a session missing from
        # the stored minutes) they would read the last session's and act on stale levels.
        if (
            now is not None
            and now.time() >= FIRST_CANDLE_DONE
            and needs_minutes([*rules.rules, *rules.any_rules])
            and not built.minutes.empty
            and last_minute_day(built) != now.date()
        ):
            return [*reasons, "entry rule: no one-minute candles for this session"]
        reasons += rules_failures(rules.rules, built)
        if rules.any_rules and all(rule_failure(rule, built) for rule in rules.any_rules):
            reasons.append("entry rule: none of the any-of rules holds")
    return reasons


def _has_market_rules(spec: StrategySpec) -> bool:
    entry = spec.entry
    return bool(
        entry.trend
        or entry.conditions
        or entry.iv_rank_min is not None
        or entry.iv_rank_max is not None
    )


def entry_failures(
    spec: StrategySpec, ctx: MarketContext, front_expiry: date | None = None
) -> list[str]:
    rules = spec.entry
    # The clock is read only by time rules, so callers without one still work.
    reasons = quick_failures(
        spec, getattr(ctx, "now", None), lambda: Frames(ctx.bars, getattr(ctx, "intraday", None))
    )
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


def _number(value) -> float | None:
    return None if value is None or pd.isna(value) else round(float(value), 2)


def entry_reasons(
    spec: StrategySpec, ctx: MarketContext, front_expiry: date | None = None
) -> list[dict]:
    """Why the entry rules held at this decision: each rule with the values it read, for the
    trade's record. Only the any-of rules that held are listed."""
    entry, found = spec.entry, []
    now = getattr(ctx, "now", None)
    if entry.time_from or entry.time_to:
        start = f"{entry.time_from:%H:%M}" if entry.time_from else "09:15"
        end = f"{entry.time_to:%H:%M}" if entry.time_to else "15:25"
        found.append(
            {
                "rule": f"entry time between {start} and {end}",
                "holds": True,
                "value_text": f"{now:%H:%M}" if now else None,
            }
        )
    if entry.rules or entry.any_rules:
        frames = Frames(ctx.bars, getattr(ctx, "intraday", None))
        for index, rule in enumerate(entry.rules):
            reading = rule_reading(rule, frames)
            # How it joins the rule before: with "or", a rule may not hold while the trade does.
            found.append(reading | ({"join": rule.join} if index else {}))
        for rule in entry.any_rules:
            reading = rule_reading(rule, frames)
            if reading["holds"]:
                found.append(reading | {"any_of": True})
    if entry.trend:
        trend = ctx.regime.trend if ctx.regime else None
        found.append(
            {"rule": f"trend is {' or '.join(entry.trend)}", "holds": True, "value_text": trend}
        )
    if entry.iv_rank_min is not None or entry.iv_rank_max is not None:
        ranking = ctx.iv.ranking
        low = "0" if entry.iv_rank_min is None else f"{entry.iv_rank_min:g}"
        high = "100" if entry.iv_rank_max is None else f"{entry.iv_rank_max:g}"
        found.append(
            {
                "rule": f"IV rank between {low} and {high}",
                "holds": True,
                "left": ranking.rank if ranking else None,
            }
        )
    for condition in entry.conditions:
        found.append(
            {
                "rule": f"{SIGNALS[condition.signal]} {condition.op} {condition.value:g}",
                "holds": True,
                "left": signal_value(condition.signal, ctx, front_expiry),
                "right": condition.value,
                "left_name": SIGNALS[condition.signal],
                "right_name": f"{condition.value:g}",
                "op": condition.op,
            }
        )
    for reading in found:
        for key in ("left", "right", "left_before", "right_before"):
            if key in reading:
                reading[key] = _number(reading[key])
    return found


STOPPED_OUT = {"stop", "trailing_stop", "stop_at_cost", "leg_stop"}
TARGET_HIT = {"target", "leg_target"}


@dataclass
class _Wait:
    day: date
    candidate: Candidate
    # The net premium per unit when the rules held.
    premium: float


def _premium(legs) -> float:
    """Net premium per lot unit at mid: what a debit costs or a credit brings in."""
    return abs(sum(leg.side * leg.ratio * leg.mid for leg in legs))


def _wait_reason(entry: EntryRules, start: float, now: float | None) -> str:
    amount = f"{entry.wait_move:g}%" if entry.wait_unit == "pct" else f"{entry.wait_move:g} points"
    seen = "" if now is None else f", now {now:.2f}"
    return f"wait and trade: premium {start:.2f} to move {entry.wait_direction} {amount}{seen}"


def _day(moment: str | None):
    return None if moment is None else datetime.fromisoformat(moment).date()


ATR_PERIOD = 14
ATR_YEAR = 252
ATR_MIN_HISTORY = 60


def volatility_scale(budget: Budget, bars: pd.DataFrame) -> float:
    """How much of its usual budget a trade gets: the year's median ATR share of price over
    today's, kept between the floor and the cap. 1 when sizing ignores volatility or there is
    too little history."""
    if budget.vol_sizing == "off" or len(bars) < ATR_MIN_HISTORY:
        return 1.0
    high, low, close = bars["high"], bars["low"], bars["close"]
    true_range = pd.concat(
        [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1
    ).max(axis=1)
    share = (true_range.ewm(alpha=1 / ATR_PERIOD, adjust=False).mean() / close).iloc[-ATR_YEAR:]
    now, usual = float(share.iloc[-1]), float(share.median())
    if not now > 0 or not usual > 0:
        return 1.0
    return min(max(usual / now, budget.vol_floor), budget.vol_cap)


def _add_weekdays(day: date, count: int) -> date:
    return np.busday_offset(np.datetime64(day), count, roll="forward").astype(date)


def breaker_pause(budget: Budget, closed: list[tuple[date, float]]) -> tuple[date, str] | None:
    """The last pause the circuit breakers set, from closed trades in order: (until, why).

    Each pause starts the count again, so one losing run pauses the strategy once."""
    if budget.pause_after_losses is None and budget.pause_drawdown_pct is None:
        return None
    streak, equity, peak = 0, 0.0, 0.0
    pause = None
    for day, pnl in closed:
        equity += pnl
        peak = max(peak, equity)
        streak = streak + 1 if pnl < 0 else 0
        why = None
        if budget.pause_after_losses is not None and streak >= budget.pause_after_losses:
            why = f"{streak} losing trades in a row"
        elif (
            budget.pause_drawdown_pct is not None
            and peak - equity >= budget.capital * budget.pause_drawdown_pct / 100
        ):
            why = f"down {budget.pause_drawdown_pct:g}% of capital from its best"
        if why:
            pause = (_add_weekdays(day, budget.pause_sessions), why)
            streak, peak = 0, equity
    return pause


class CustomSet:
    """Feeds custom strategies into the normal idea pipeline, and enforces their exit rules
    and daily limits on the positions they opened."""

    def __init__(self, specs: list[StrategySpec], only: bool = True):
        self.specs = specs
        self.only = only
        self.no_contracts: Counter = Counter()
        # The broker whose positions the daily limits count. Set by the cycle.
        self.broker = None
        # Wait and trade: signals waiting for their premium to move, by (strategy, symbol,
        # case).
        self.waiting: dict[tuple[str, str, int], _Wait] = {}

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

    def breaker_failure(self, spec: StrategySpec, positions: list, today) -> str | None:
        closed = sorted(
            (p for p in positions if p.status == "closed" and p.closed_at),
            key=lambda p: p.closed_at,
        )
        pause = breaker_pause(
            spec.budget, [(_day(p.closed_at), p.realized_pnl or 0.0) for p in closed]
        )
        if pause is None or today > pause[0]:
            return None
        return f"circuit breaker: {pause[1]}, paused until {pause[0]:%d %b %Y}"

    def limit_failures(self, spec: StrategySpec, today) -> list[str]:
        """Why the strategy may not open another trade today."""
        entry, exits = spec.entry, spec.exits
        budget = spec.budget
        breaker = None
        if budget.pause_after_losses is not None or budget.pause_drawdown_pct is not None:
            breaker = self.breaker_failure(spec, self._positions(spec), today)
        if (
            breaker is None
            and entry.max_trades_per_day is None
            and entry.reenter_after_stop
            and entry.reenter_after_target
            and exits.daily_stop is None
            and exits.daily_target is None
        ):
            return []
        positions = self._positions(spec)
        reasons = [breaker] if breaker else []
        today_trades = [p for p in positions if _day(p.created_at) == today]
        if entry.max_trades_per_day is not None and len(today_trades) >= entry.max_trades_per_day:
            reasons.append("entry rule: the day's trade limit is reached")
        if not entry.reenter_after_stop and any(
            p.status == "closed" and _day(p.closed_at) == today and p.exit_reason in STOPPED_OUT
            for p in positions
        ):
            reasons.append("entry rule: no re-entry after a stop today")
        if not entry.reenter_after_target and any(
            p.status == "closed" and _day(p.closed_at) == today and p.exit_reason in TARGET_HIT
            for p in positions
        ):
            reasons.append("entry rule: no new trade after a target today")
        pnl = self._day_pnl(positions, today)
        if exits.daily_stop is not None and pnl <= -exits.daily_stop:
            reasons.append("entry rule: the day's loss limit is reached")
        if exits.daily_target is not None and pnl >= exits.daily_target:
            reasons.append("entry rule: the day's profit target is reached")
        return reasons

    def idle_reasons(
        self, symbol: str, now: datetime, frames: Callable[[], Frames]
    ) -> list[str] | None:
        """Why no strategy can enter on `symbol` now, from checks that need no option chain.

        Returns None when any strategy might enter, so the full cycle must run. Otherwise one
        reason per strategy, the same first reason the full cycle would give it. Built once,
        the candles are shared by every strategy."""
        cache: list[Frames] = []

        def shared() -> Frames:
            if not cache:
                cache.append(frames())
            return cache[0]

        reasons = []
        for spec in self.specs:
            if spec.symbols and symbol not in spec.symbols:
                continue
            if any(
                key[:2] == (spec.id, symbol) and wait.day == now.date()
                for key, wait in self.waiting.items()
            ):
                return None
            # Idle only when every case fails; the first case's reason is reported.
            per_case = [quick_failures(variant, now, shared) for variant in variants(spec)]
            failures = [] if any(not found for found in per_case) else per_case[0]
            # A day limit is reported after the market rules, so it can only stand in for them
            # when there are none.
            if not failures and not _has_market_rules(spec):
                failures = self.limit_failures(spec, now.date())
            if not failures:
                return None
            reasons.append(failures[0])
        return reasons

    def exit_requests(self, ctx: MarketContext) -> list[tuple[int, str]]:
        """Open positions of these strategies that their exit rules or daily limits close."""
        requests = []
        for spec in self.specs:
            exits = spec.exits
            case_rules = [variant.exits.rules for variant in variants(spec)]
            if not (any(case_rules) or exits.daily_stop or exits.daily_target):
                continue
            positions = self._positions(spec)
            mine = [p for p in positions if p.status == "open" and p.symbol == ctx.symbol]
            if not mine:
                continue
            pnl = self._day_pnl(positions, ctx.now.date())
            if exits.daily_stop is not None and pnl <= -exits.daily_stop:
                requests += [(position.id, "daily_stop") for position in mine]
                continue
            if exits.daily_target is not None and pnl >= exits.daily_target:
                requests += [(position.id, "daily_target") for position in mine]
                continue
            frames = Frames(ctx.bars, getattr(ctx, "intraday", None))
            fired: dict[int, bool] = {}
            for position in mine:
                case = getattr(position, "case", 0)
                rules = case_rules[case] if case < len(case_rules) else exits.rules
                if case not in fired:
                    fired[case] = any(rule_failure(rule, frames) is None for rule in rules)
                if fired[case]:
                    requests.append((position.id, "exit_rule"))
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
            limits = self.limit_failures(spec, ctx.now.date())
            scale = volatility_scale(spec.budget, getattr(ctx, "bars", pd.DataFrame()))
            for case, variant in enumerate(variants(spec)):
                key = (spec.id, ctx.symbol, case)
                wait = self.waiting.get(key)
                if wait is not None and wait.day != ctx.now.date():
                    del self.waiting[key]
                    wait = None
                if wait is not None:
                    found += self._waited(key, wait, spec, live, limits)
                    continue
                candidate = build_candidate(variant, ctx.symbol, views, ctx.now.date())
                if candidate is None:
                    self.no_contracts[spec.id] += 1
                    continue
                candidate = replace(candidate, case=case, size_scale=scale)
                failures = entry_failures(variant, ctx, candidate.front_expiry) + limits
                if not failures:
                    why = entry_reasons(variant, ctx, candidate.front_expiry)
                    candidate = replace(candidate, why=tuple(why))
                if not failures and spec.entry.wait_move is not None:
                    # The signal arms a wait; the trade comes once the premium has moved.
                    self.waiting[key] = _Wait(ctx.now.date(), candidate, _premium(candidate.legs))
                    failures = [_wait_reason(spec.entry, self.waiting[key].premium, None)]
                found.append((candidate, failures))
        return found

    def _waited(self, key, wait: _Wait, spec: StrategySpec, chain: pd.DataFrame, limits):
        """The waiting contracts at today's quotes, as a candidate once the move is made."""
        rows = chain.set_index("instrument_key")
        legs = []
        for leg in wait.candidate.legs:
            if leg.instrument_key not in rows.index:
                return []
            row = rows.loc[leg.instrument_key]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            row = row.copy()
            row["instrument_key"] = leg.instrument_key
            legs.append(
                replace(
                    make_leg(row, leg.side, leg.ratio),
                    stop=leg.stop,
                    stop_unit=leg.stop_unit,
                    target=leg.target,
                    target_unit=leg.target_unit,
                )
            )
        candidate = replace(wait.candidate, legs=tuple(legs))
        now = _premium(candidate.legs)
        entry = spec.entry
        move = now - wait.premium
        if entry.wait_direction == "down":
            move = -move
        needed = (
            wait.premium * entry.wait_move / 100 if entry.wait_unit == "pct" else entry.wait_move
        )
        if move < needed:
            return [(candidate, [_wait_reason(entry, wait.premium, now)])]
        del self.waiting[key]
        waited = {
            "rule": _wait_reason(entry, wait.premium, now).replace("wait and trade: ", ""),
            "holds": True,
        }
        candidate = replace(candidate, why=(*candidate.why, waited))
        return [(candidate, list(limits))]


def with_budget(services, spec: StrategySpec):
    """Services that size and limit trades for this strategy's own budget: its capital, its
    risk per trade and its open-position limit, with margin and the day's loss as shares of
    that capital. Limits on open and per-underlying risk are raised where needed, so
    `max_open` positions at full risk fit.

    The Greek and stress limits guard the whole account, so they keep the account's rupee
    amounts: on a smaller budget they would otherwise block a single lot."""
    budget = spec.budget
    risk = services.risk.model_copy(deep=True)
    share = risk.undefined_risk_loss_per_trade_pct / risk.max_loss_per_trade_pct
    scale = risk.capital / budget.capital
    risk.max_vega_pct *= scale
    risk.max_gamma_1pct_pct *= scale
    risk.max_stress_loss_pct *= scale
    risk.max_delta_notional_multiple *= scale
    risk.capital = budget.capital
    risk.max_loss_per_trade_pct = budget.risk_per_trade_pct
    risk.undefined_risk_loss_per_trade_pct = budget.risk_per_trade_pct * share
    room = budget.risk_per_trade_pct * budget.max_open
    risk.max_open_risk_pct = max(risk.max_open_risk_pct, room)
    risk.max_underlying_risk_pct = max(risk.max_underlying_risk_pct, room)
    risk.max_daily_loss_pct = max(risk.max_daily_loss_pct, budget.risk_per_trade_pct)
    risk.max_open_positions = budget.max_open
    if budget.sizing == "budget":
        risk.size_by_budget = True
        risk.max_margin_use_pct = budget.deploy_pct
        risk.max_lots_per_trade = 1000
        # Positions this size would trip the account's daily halt on an ordinary stop; the
        # strategy's own daily stop, if it has one, still applies.
        risk.max_daily_loss_pct = 100.0
    return replace(services, risk=risk)


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

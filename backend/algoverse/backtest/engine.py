"""Replays imported history through the same cycle that paper trading runs.

Each session has two steps. At the open, decisions from the previous close fill. At the close,
positions are marked and new decisions are made, which fill at the next open.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from algoverse.analytics.chain import years_to_expiry
from algoverse.analytics.pricing import black76_price
from algoverse.backtest.config import BacktestConfig
from algoverse.backtest.dataview import DataView, History
from algoverse.backtest.fills import contract_key, modelled_chain
from algoverse.data.store import SnapshotStore
from algoverse.evaluation.config import EventCalendar, ScoringConfig
from algoverse.execution.cycle import (
    VIX_SYMBOL,
    CycleDeps,
    prepare_chains,
    run_cycle,
    store_reference_iv,
)
from algoverse.execution.ledger import CANCELLED, CLOSED, SCHEMA, Ledger
from algoverse.execution.paper import PaperBroker
from algoverse.risk.config import RiskConfig
from algoverse.risk.costs import CostModel

IST = ZoneInfo("Asia/Kolkata")
OPEN_TIME = time(9, 15)
MIDDAY_TIME = time(12, 30)
CLOSE_TIME = time(15, 25)
END_TIME = time(15, 35)


class MemoryLedger(Ledger):
    """The normal ledger, on one in-memory database for speed."""

    def __init__(self):
        self.path = Path(":memory:")
        self._connection = sqlite3.connect(":memory:")
        self._connection.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._connection:
            yield self._connection


@dataclass(frozen=True)
class ReplaySpec:
    start: date
    end: date
    min_score: float
    disabled_profiles: tuple[str, ...] = ()
    spread_multiplier: float = 1.0
    # For one chunk of a longer window: that window's start. Warm-up then begins where the
    # whole window's would, so volatility history, and every decision, matches a single run.
    window_start: date | None = None


@dataclass
class ReplayResult:
    spec: ReplaySpec
    trades: pd.DataFrame
    daily_pnl: pd.Series
    sessions: int
    unfilled: dict[str, int] = field(default_factory=dict)
    # One entry per closed trade with its legs, for showing trades on a chart.
    details: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class Services:
    backtest: BacktestConfig
    scoring: ScoringConfig
    risk: RiskConfig
    costs: CostModel
    events: EventCalendar


def _at(day: date, moment: time) -> datetime:
    return datetime.combine(day, moment, tzinfo=IST)


def _prepare_options(
    day: pd.DataFrame, previous: pd.DataFrame | None, symbol: str, config: BacktestConfig
) -> pd.DataFrame:
    options = day[(day["symbol"] == symbol) & (day["instrument"] == "OPT")]
    session = options["date"].iloc[0] if not options.empty else None
    if options.empty:
        return options.assign(prev_close=pd.Series(dtype=float))
    expiries = sorted(e for e in options["expiry"].unique() if e >= session)
    keep = expiries[: config.expiries_per_underlying]
    options = options[options["expiry"].isin(keep)].copy()
    earlier = (
        previous[(previous["symbol"] == symbol) & (previous["instrument"] == "OPT")]
        if previous is not None
        else None
    )
    if earlier is None or earlier.empty:
        options["prev_close"] = options["close"]
        return options
    prior = dict(zip(contract_key(symbol, earlier), earlier["close"].to_numpy(float), strict=True))
    close = contract_key(symbol, options).map(prior)
    options["prev_close"] = close.fillna(options["close"]).to_numpy(float)
    return options


def _spot(day: pd.DataFrame, symbol: str, session: date, bar: pd.Series) -> float:
    rows = day[(day["symbol"] == symbol) & (day["underlying_price"] > 0)]
    return float(rows["underlying_price"].iloc[0]) if not rows.empty else float(bar["close"])


def _extremes(position, bar: pd.Series, ivs: dict[str, float], now: datetime, r: float):
    """P&L of a position at the bar's high and low, and each leg's (low, high) price, or None
    if a leg has no volatility. Legs already closed on their own stop or target keep their
    exit price."""
    results = []
    legs: list[list[float]] = [[] for _ in position.legs]
    for spot in (float(bar["high"]), float(bar["low"])):
        total = 0.0
        for index, leg in enumerate(position.legs):
            if leg.closed:
                value = leg.exit_price
            else:
                iv = ivs.get(leg.instrument_key)
                if iv is None or not np.isfinite(iv):
                    return None
                T = max(years_to_expiry(date.fromisoformat(leg.expiry), now), 0.0)
                value = float(
                    black76_price(
                        spot * np.exp(r * T), leg.strike, T, iv, r, leg.option_type == "CE"
                    )
                )
            legs[index].append(value)
            total += leg.side * (value - leg.entry_price) * leg.units
        results.append(total)
    return min(results), max(results), [(min(values), max(values)) for values in legs]


def _bar_extremes(bar_by_day: dict, session: date, ivs: dict, now: datetime, r: float, position):
    bar = bar_by_day.get(position.symbol, {}).get(session)
    return None if bar is None else _extremes(position, bar, ivs, now, r)


def regime_label(context) -> tuple[str, str]:
    trend = context.regime.trend if context.regime else "unknown"
    ranking = context.iv.ranking
    if ranking is None:
        return trend, "unknown"
    return trend, "low" if ranking.rank < 33 else "high" if ranking.rank > 66 else "mid"


def trades_frame(ledger: Ledger, labels: dict[int, dict]) -> pd.DataFrame:
    rows = []
    for position in ledger.positions((CLOSED,)):
        opened = datetime.fromisoformat(position.opened_at).date()
        closed = datetime.fromisoformat(position.closed_at).date()
        rows.append(
            {
                "symbol": position.symbol,
                "strategy": position.strategy,
                "profile": position.profile,
                "lots": position.lots,
                "margin": position.margin,
                "opened": opened,
                "closed": closed,
                "holding_days": (closed - opened).days,
                "dte_at_entry": (position.front_expiry - opened).days,
                "score": position.score,
                "exit_reason": position.exit_reason,
                "net_pnl": position.realized_pnl,
                "costs": position.entry_costs + position.exit_costs,
                "settlement_estimated": position.settlement_estimated,
            }
            | labels.get(position.id, {"trend": "unknown", "iv_regime": "unknown"})
        )
    return pd.DataFrame(rows)


def trade_details(ledger: Ledger, labels: dict[int, dict]) -> list[dict]:
    """Closed trades with their legs, in the order they opened."""
    rows = []
    for position in ledger.positions((CLOSED,)):
        rows.append(
            {
                "symbol": position.symbol,
                "strategy": position.strategy,
                "lots": position.lots,
                "opened": datetime.fromisoformat(position.opened_at).date().isoformat(),
                "closed": datetime.fromisoformat(position.closed_at).date().isoformat(),
                "opened_at": position.opened_at,
                "closed_at": position.closed_at,
                "exit_reason": position.exit_reason,
                "net_pnl": float(position.realized_pnl or 0.0),
                "costs": float(position.entry_costs + position.exit_costs),
                "planned_loss": float(position.planned_loss),
                "target_pnl": float(position.target_pnl),
                # Exit levels in rupees for the whole position, against its fill prices.
                "stop_pnl": float(position.stop_pnl),
                "trail_pnl": None if position.trail_pnl is None else float(position.trail_pnl),
                "breakeven_pnl": None
                if position.breakeven_pnl is None
                else float(position.breakeven_pnl),
                "settlement_estimated": position.settlement_estimated,
                "case": position.case,
                # When the rules held and the order was placed; it fills at the next check.
                "decided_at": position.created_at,
                "why": position.why,
                "entry_spot": position.entry_spot,
                "legs": [
                    {
                        "side": "buy" if leg.side > 0 else "sell",
                        "option_type": leg.option_type,
                        "strike": float(leg.strike),
                        "expiry": leg.expiry,
                        "units": int(leg.units),
                        "entry_price": leg.entry_price,
                        "exit_price": leg.exit_price,
                        "stop_price": leg.stop_price,
                        "target_price": leg.target_price,
                        "entry_delta": leg.entry_delta,
                        "exit_reason": leg.exit_reason,
                        "closed_at": leg.closed_at,
                    }
                    for leg in position.legs
                ],
            }
            | labels.get(position.id, {"trend": "unknown", "iv_regime": "unknown"})
        )
    return sorted(rows, key=lambda row: (row["opened"], row["closed"]))


def daily_pnl_series(ledger: Ledger) -> pd.Series:
    series = ledger.equity_series(limit=10_000_000)
    if not series:
        return pd.Series(dtype=float)
    frame = pd.DataFrame(series, columns=["ts", "pnl"])
    frame["day"] = pd.to_datetime(frame["ts"].str[:10]).dt.date
    last = frame.groupby("day")["pnl"].last()
    return last.diff().fillna(last)


def replay(
    history: History,
    services: Services,
    spec: ReplaySpec,
    reference: dict[str, pd.DataFrame] | None = None,
    custom=None,
    on_cycle=None,
) -> ReplayResult:
    """Run one configuration over a window, with warm-up sessions that do not trade."""
    config = services.backtest
    risk = services.risk.model_copy(deep=True)
    risk.paper.min_score = spec.min_score
    risk.paper.disabled_profiles = list(spec.disabled_profiles)
    risk.paper.idea_ttl_minutes = config.idea_ttl_minutes
    risk.paper.max_entry_drift_pct = config.max_entry_drift_pct

    all_sessions = history.sessions
    start_index = next((i for i, s in enumerate(all_sessions) if s >= spec.start), None)
    if start_index is None:
        raise ValueError(f"No imported data on or after {spec.start}")
    first = max(0, start_index - config.warmup_sessions)
    sessions = [s for s in all_sessions[first:] if s <= spec.end]

    bars = {symbol: history.bars(symbol) for symbol in config.symbols}
    view = DataView({**bars, **(reference or {})})
    ledger = MemoryLedger()
    broker = PaperBroker(ledger, risk, services.costs)
    deps = CycleDeps(
        store=view,  # type: ignore[arg-type]
        broker=broker,
        scoring=services.scoring,
        risk=risk,
        costs=services.costs,
        events=services.events,
        ai=None,
        publish=False,
        custom=custom,
        entry_block=lambda symbol: history.entry_block(view.as_of, symbol),
    )
    bar_by_day = {
        symbol: {ts.date(): row for ts, (_, row) in zip(frame["ts"], frame.iterrows(), strict=True)}
        for symbol, frame in bars.items()
    }
    # Decisions are made at the close, so the session's own candle is known to candle rules.
    current: dict[str, date] = {}

    def today_bar(symbol: str) -> dict | None:
        bar = bar_by_day.get(symbol, {}).get(current.get("session"))
        return None if bar is None else {k: float(bar[k]) for k in ("open", "high", "low", "close")}

    deps.today_bars = today_bar
    r = services.scoring.risk_free_rate
    labels: dict[int, dict] = {}
    previous_day: pd.DataFrame | None = None
    last_close: tuple[dict[str, pd.DataFrame], pd.DataFrame] | None = None
    traded_sessions = 0

    for session in sessions:
        day = history.day(session)
        view.as_of = session
        current["session"] = session
        risk.paper.auto_trade = spec.start <= session
        open_chains, close_chains, lots = {}, {}, []
        open_at, close_at = _at(session, OPEN_TIME), _at(session, CLOSE_TIME)
        for symbol in config.symbols:
            bar = bar_by_day[symbol].get(session)
            options = _prepare_options(day, previous_day, symbol, config)
            if bar is None or options.empty:
                continue
            spot = _spot(day, symbol, session, bar)
            spot_low, spot_high = spot * (1 - config.strike_range), spot * (1 + config.strike_range)
            options = options[options["strike"].between(spot_low, spot_high)]
            open_spot = float(bar["open"]) if bar["open"] > 0 else spot
            open_chains[symbol] = modelled_chain(
                symbol, options, "open", open_spot, open_at, config.spread, spec.spread_multiplier
            )
            close_chains[symbol] = modelled_chain(
                symbol, options, "close", spot, close_at, config.spread, spec.spread_multiplier
            )
            lots.append(
                pd.DataFrame(
                    {
                        "instrument_key": contract_key(symbol, options).to_numpy(),
                        "lot_size": options["lot_size"].to_numpy(),
                    }
                )
            )
        previous_day = day
        if not close_chains:
            continue
        contracts = pd.concat(lots, ignore_index=True)
        if session < spec.start:
            # Warm-up only needs the volatility history that later rankings read.
            for symbol, chain in prepare_chains(close_chains, contracts, close_at, r).items():
                store_reference_iv(view, symbol, chain, close_at)
            continue
        enriched_open = prepare_chains(open_chains, contracts, open_at, r)
        report = broker.step(open_at, enriched_open)

        # A daily bar hides the intraday path, so stops are checked at both extremes first.
        ivs = {}
        for chain in enriched_open.values():
            ivs.update(zip(chain["instrument_key"], chain["iv"].to_numpy(float), strict=True))
        midday = _at(session, MIDDAY_TIME)
        broker.check_intrabar(
            midday, partial(_bar_extremes, bar_by_day, session, ivs, midday, r), report
        )

        result = run_cycle(deps, close_chains, contracts, close_at)
        if on_cycle is not None:
            on_cycle(result)
        traded_sessions += 1
        for position_id in result.submitted:
            symbol = next(p.symbol for p in ledger.positions(("pending",)) if p.id == position_id)
            trend, iv_regime = regime_label(result.contexts[symbol])
            labels[position_id] = {"trend": trend, "iv_regime": iv_regime}
        last_close = (close_chains, contracts)

    if last_close is not None:
        end_at = _at(sessions[-1], END_TIME)
        broker.flatten(end_at, prepare_chains(last_close[0], last_close[1], end_at, r))

    unfilled: dict[str, int] = {}
    for position in ledger.positions((CANCELLED,)):
        reason = (position.exit_reason or "unknown").split(":")[0].split(" for ")[0]
        unfilled[reason] = unfilled.get(reason, 0) + 1
    daily = daily_pnl_series(ledger)
    daily = daily[[d >= spec.start for d in daily.index]] if len(daily) else daily
    return ReplayResult(
        spec,
        trades_frame(ledger, labels),
        daily,
        traded_sessions,
        unfilled,
        trade_details(ledger, labels),
    )


def reference_series(store: SnapshotStore) -> dict[str, pd.DataFrame]:
    """India VIX from collected data, when available. Other series come from history."""
    vix = store.read_candles(VIX_SYMBOL)
    return {} if vix.empty else {VIX_SYMBOL: vix.sort_values("ts")}

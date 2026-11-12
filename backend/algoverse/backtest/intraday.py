"""Replays sessions minute by minute for strategies that decide during the day.

Decisions are made every 5, 15 or 60 minutes from 09:15. At each decision time:
1. Stops and targets are checked against the index's high and low since the last decision.
2. Orders from the last decision fill, and open positions are marked, at this moment's prices.
3. New decisions are made, which fill at the next decision time.
4. Exits triggered at this moment (stop, target, square-off) fill at its prices.

Option prices are the last finished 5-minute bar of the day; a contract that has not traded
yet today has no quote. Spot is the index's one-minute close. Spreads are modelled from the
previous session's volume, so the day's own volume is never used before it happens.

Only underlyings with imported 5-minute option bars and one-minute index candles are traded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import partial

import numpy as np
import pandas as pd

from algoverse.backtest.dataview import DataView, History
from algoverse.backtest.engine import (
    IST,
    MemoryLedger,
    ReplayResult,
    ReplaySpec,
    Services,
    _extremes,
    _prepare_options,
    daily_pnl_series,
    regime_label,
    trade_details,
    trades_frame,
)
from algoverse.backtest.fills import contract_key, modelled_chain
from algoverse.evaluation.context import decision_bars
from algoverse.execution.cycle import CycleDeps, prepare_chains, run_cycle
from algoverse.execution.ledger import LIVE_STATUSES
from algoverse.execution.paper import PaperBroker, StepReport
from algoverse.lab.technical import Frames

BAR_MINUTES = 5
SESSION_OPEN = time(9, 15)
LAST_DECISION = time(15, 25)
SESSION_BARS = 75  # 09:15 to 15:30 in 5-minute bars.
# Rules on minute candles look back this many sessions, enough for long hourly averages.
LOOKBACK_SESSIONS = 40


def decision_times(session: date, every: int) -> list[datetime]:
    first = datetime.combine(session, SESSION_OPEN, IST)
    last = datetime.combine(session, LAST_DECISION, IST)
    times, moment = [], first + timedelta(minutes=every)
    while moment <= last:
        times.append(moment)
        moment += timedelta(minutes=every)
    return times


@dataclass
class _Minutes:
    """One underlying's one-minute candles, indexed for fast slicing by time."""

    frame: pd.DataFrame
    t: np.ndarray  # Start of each minute, epoch ms.
    starts: dict[date, int]

    @classmethod
    def build(cls, frame: pd.DataFrame) -> _Minutes:
        frame = frame.sort_values("ts").reset_index(drop=True)
        times = pd.to_datetime(frame["ts"])
        local = times.dt.tz_convert(IST) if times.dt.tz is not None else times.dt.tz_localize(IST)
        frame = frame.assign(ts=local)[["ts", "open", "high", "low", "close"]]
        # Parquet may return micro- or nanoseconds; count milliseconds either way.
        t = local.dt.as_unit("ms").astype("int64").to_numpy()
        days = local.dt.date.to_numpy()
        starts: dict[date, int] = {}
        for index, day in enumerate(days):
            starts.setdefault(day, index)
        return cls(frame, t, starts)

    def until(self, moment: datetime) -> int:
        """Index just past the last minute that ended by `moment`."""
        return int(np.searchsorted(self.t, int(moment.timestamp() * 1000) - 60_000, side="right"))

    def window(self, start: datetime, end: datetime) -> pd.DataFrame:
        low = int(np.searchsorted(self.t, int(start.timestamp() * 1000), side="left"))
        return self.frame.iloc[low : self.until(end)]


@dataclass
class _Chain:
    """One underlying's option prices through a session, in 5-minute steps."""

    options: pd.DataFrame
    close: np.ndarray  # contracts x bars, carried forward within the day.
    traded: np.ndarray  # contracts x bars, True once the contract has traded today.
    oi: np.ndarray

    @classmethod
    def build(
        cls, options: pd.DataFrame, bars: pd.DataFrame, previous: pd.DataFrame | None, session
    ) -> _Chain | None:
        if options.empty or bars.empty:
            return None
        options = options.reset_index(drop=True)
        index = {
            key: i
            for i, key in enumerate(
                zip(options["expiry"], options["strike"], options["option_type"], strict=True)
            )
        }
        opening = int(datetime.combine(session, SESSION_OPEN, IST).timestamp() * 1000)
        rows = np.array(
            [
                index.get((e, s, o), -1)
                for e, s, o in zip(
                    pd.to_datetime(bars["expiry"]).dt.date,
                    bars["strike"].astype(float),
                    bars["option_type"],
                    strict=True,
                )
            ]
        )
        steps = ((bars["t"].to_numpy(np.int64) - opening) // (BAR_MINUTES * 60_000)).astype(int)
        keep = (rows >= 0) & (steps >= 0) & (steps < SESSION_BARS)
        shape = (len(options), SESSION_BARS)
        close = np.full(shape, np.nan)
        volume = np.zeros(shape)
        oi = np.full(shape, np.nan)
        close[rows[keep], steps[keep]] = bars["close"].to_numpy(float)[keep]
        volume[rows[keep], steps[keep]] = bars["volume"].to_numpy(float)[keep]
        oi[rows[keep], steps[keep]] = bars["oi"].to_numpy(float)[keep]
        close = pd.DataFrame(close).ffill(axis=1).to_numpy()
        oi = pd.DataFrame(oi).ffill(axis=1).to_numpy()
        traded = np.cumsum(volume, axis=1) > 0
        # Spreads use the previous session's volume, which is known before the day starts.
        if previous is not None and not previous.empty:
            previous = previous[
                (previous["instrument"] == "OPT")
                & (previous["symbol"] == options["symbol"].iloc[0])
            ]
            before = dict(
                zip(contract_key("", previous), previous["volume"].to_numpy(float), strict=True)
            )
            prior = contract_key("", options).map(before).fillna(0.0).to_numpy(float)
        else:
            prior = np.zeros(len(options))
        options = options.assign(volume=np.maximum(prior, 1.0))
        return cls(options, close, traded, oi)

    def at(self, moment: datetime) -> pd.DataFrame:
        """Option prices known at `moment`: the last finished 5-minute bar of the day."""
        opening = datetime.combine(moment.date(), SESSION_OPEN, IST)
        finished = int((moment - opening).total_seconds() // (BAR_MINUTES * 60))
        if finished <= 0:
            return self.options.iloc[0:0]
        column = min(finished, SESSION_BARS) - 1
        price = np.where(self.traded[:, column], self.close[:, column], np.nan)
        oi = self.oi[:, column]
        frame = self.options.assign(close=price, oi=np.where(np.isnan(oi), self.options["oi"], oi))
        return frame[np.isfinite(price) & (price > 0)]


def replay_intraday(
    history: History,
    services: Services,
    spec: ReplaySpec,
    minutes: dict[str, pd.DataFrame],
    every: int,
    reference: dict[str, pd.DataFrame] | None = None,
    custom=None,
    on_cycle=None,
    on_skip=None,
    fast: bool = True,
) -> ReplayResult:
    """Run one configuration over a window, deciding every `every` minutes.

    With `fast`, a decision is skipped when nothing is open or pending and every custom
    strategy fails a check that needs no option chain (its time window, candle rules or day
    limits). Such a decision cannot change anything, so results are the same; `on_skip` gets
    the reasons, one per strategy, for the skipped-days report. The last decision of each
    session always runs, since it records the day's volatility and P&L."""
    if every % BAR_MINUTES:
        raise ValueError(f"Decisions must be a multiple of {BAR_MINUTES} minutes")
    config = services.backtest
    risk = services.risk.model_copy(deep=True)
    risk.paper.min_score = spec.min_score
    risk.paper.disabled_profiles = list(spec.disabled_profiles)
    # An order fills at the next decision time or not at all, so none carries overnight.
    risk.paper.idea_ttl_minutes = every + 1
    risk.paper.max_entry_drift_pct = config.max_entry_drift_pct

    indexed = {s: _Minutes.build(f) for s, f in minutes.items() if s in config.symbols and len(f)}
    sessions_all = [s for s in history.sessions if history.has_intraday(s)]
    anchor = spec.window_start or spec.start
    if next((s for s in sessions_all if s >= spec.start), None) is None:
        raise ValueError(f"No intraday data on or after {spec.start}")
    start_index = next((i for i, s in enumerate(sessions_all) if s >= anchor), None)
    if start_index is None:
        raise ValueError(f"No intraday data on or after {spec.start}")
    first = max(0, start_index - config.warmup_sessions)
    sessions = [s for s in sessions_all[first:] if s <= spec.end]

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
    )
    now: dict[str, datetime] = {}

    def today_bar(symbol: str) -> dict | None:
        series = indexed.get(symbol)
        moment = now.get("t")
        if series is None or moment is None:
            return None
        today = series.window(datetime.combine(moment.date(), SESSION_OPEN, IST), moment)
        if today.empty:
            return None
        return {
            "open": float(today["open"].iloc[0]),
            "high": float(today["high"].max()),
            "low": float(today["low"].min()),
            "close": float(today["close"].iloc[-1]),
        }

    def intraday_bars(symbol: str) -> pd.DataFrame:
        series = indexed.get(symbol)
        moment = now.get("t")
        if series is None or moment is None:
            return pd.DataFrame()
        days = [d for d in series.starts if d <= moment.date()]
        first_day = days[max(0, len(days) - LOOKBACK_SESSIONS)]
        return series.frame.iloc[series.starts[first_day] : series.until(moment)]

    deps.today_bars = today_bar
    deps.intraday_bars = intraday_bars
    can_skip = fast and getattr(custom, "only", False) and hasattr(custom, "idle_reasons")
    r = services.scoring.risk_free_rate
    labels: dict[int, dict] = {}
    previous_day: pd.DataFrame | None = None
    last: tuple[dict[str, pd.DataFrame], pd.DataFrame] | None = None
    traded_sessions = 0

    for session in sessions:
        day = history.day(session)
        view.as_of = session
        risk.paper.auto_trade = spec.start <= session
        option_bars = history.intraday(session)
        chains: dict[str, _Chain] = {}
        lots = []
        for symbol in config.symbols:
            if symbol not in indexed or session not in indexed[symbol].starts:
                continue
            options = _prepare_options(day, previous_day, symbol, config)
            mine = option_bars[option_bars["symbol"] == symbol] if len(option_bars) else option_bars
            built = _Chain.build(options, mine, previous_day, session)
            if built is None:
                continue
            chains[symbol] = built
            lots.append(
                pd.DataFrame(
                    {
                        "instrument_key": contract_key(symbol, options).to_numpy(),
                        "lot_size": options["lot_size"].to_numpy(),
                    }
                )
            )
        previous_day = day
        if not chains:
            continue
        contracts = pd.concat(lots, ignore_index=True)
        previous_moment = datetime.combine(session, SESSION_OPEN, IST)
        ivs: dict[str, float] = {}
        times = decision_times(session, every)
        for moment in times:
            now["t"] = moment
            if can_skip and session >= spec.start and moment != times[-1]:
                reasons = _idle(custom, chains, moment, view, today_bar, intraday_bars, ledger)
                if reasons is not None:
                    previous_moment = moment
                    if on_skip is not None:
                        on_skip(reasons)
                    continue
            raw = {}
            ranges = {}
            for symbol, chain in chains.items():
                interval = indexed[symbol].window(previous_moment, moment)
                if interval.empty:
                    continue
                spot = float(interval["close"].iloc[-1])
                ranges[symbol] = pd.Series(
                    {"high": float(interval["high"].max()), "low": float(interval["low"].min())}
                )
                options = chain.at(moment)
                band = (options["strike"] / spot - 1).abs() <= config.strike_range
                raw[symbol] = modelled_chain(
                    symbol,
                    options[band],
                    "close",
                    spot,
                    moment,
                    config.spread,
                    spec.spread_multiplier,
                )
            previous_moment = moment
            if not raw:
                continue
            if session < spec.start:
                # Warm-up only needs the day's volatility history, kept from the last decision.
                if moment == decision_times(session, every)[-1]:
                    run_cycle(deps, raw, contracts, moment, trade=True)
                continue
            # The index's range since the last decision can trigger a stop or target first.
            broker.check_intrabar(
                moment, partial(_range_extremes, ranges, ivs, moment, r), StepReport()
            )
            # The cycle fills earlier orders and marks positions at these prices, then decides.
            result = run_cycle(deps, raw, contracts, moment)
            broker.fill_exits(moment, result.chains)
            for chain in result.chains.values():
                ivs.update(zip(chain["instrument_key"], chain["iv"].to_numpy(float), strict=True))
            if on_cycle is not None:
                on_cycle(result)
            for position_id in result.submitted:
                symbol = next(
                    p.symbol for p in ledger.positions(("pending",)) if p.id == position_id
                )
                trend, iv_regime = regime_label(result.contexts[symbol])
                labels[position_id] = {"trend": trend, "iv_regime": iv_regime}
            last = (raw, contracts)
        if session >= spec.start:
            traded_sessions += 1

    if last is not None:
        end_at = datetime.combine(sessions[-1], time(15, 35), IST)
        broker.flatten(end_at, prepare_chains(last[0], last[1], end_at, r))
    daily = daily_pnl_series(ledger)
    daily = daily[[d >= spec.start for d in daily.index]] if len(daily) else daily
    return ReplayResult(
        spec,
        trades_frame(ledger, labels),
        daily,
        traded_sessions,
        {},
        trade_details(ledger, labels),
    )


def _idle(custom, chains, moment, view, today_bar, intraday_bars, ledger) -> list[str] | None:
    """Reasons nothing can happen at this decision, or None when the full cycle must run."""
    if ledger.positions(LIVE_STATUSES):
        return None
    reasons: list[str] = []
    for symbol in chains:

        def frames(symbol: str = symbol) -> Frames:
            bars = decision_bars(view.read_candles(symbol), moment, today_bar(symbol))
            return Frames(bars, intraday_bars(symbol))

        found = custom.idle_reasons(symbol, moment, frames)
        if found is None:
            return None
        reasons += found
    return reasons


def _range_extremes(ranges: dict, ivs: dict, now: datetime, r: float, position):
    bar = ranges.get(position.symbol)
    return None if bar is None else _extremes(position, bar, ivs, now, r)

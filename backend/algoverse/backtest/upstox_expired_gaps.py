"""Fill holes in the option history with expired contracts, which needs Upstox Plus.

A backtest reads each session's nearest few expiries per underlying. This finds every
(underlying, expiry) that some stored or missing session should hold but does not, and fetches
only those contracts, so a hole of a few weeks costs a few thousand requests rather than a
full re-import:

- sessions missing altogether are written from the fetched contracts and the index candle,
- sessions that lack an underlying or an expiry get the missing rows added; rows already
  there are never changed,
- underlyings that have 5-minute option bars elsewhere in the history get them here too, from
  the same request, so intraday backtests stay continuous.

Expiries Upstox no longer serves (before about October 2024) cannot be fetched. Sessions whose
nearest expiry is among them are recorded in `incomplete.json`, and backtests open no new trade
on that underlying there. Marks for sessions that are now complete are removed.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from algoverse.backtest.bhavcopy import COLUMNS, history_dir, store_day
from algoverse.backtest.candle_files import (
    INTRADAY_MINUTES,
    _daily_index,
    _index_rows,
    intraday_dir,
)
from algoverse.backtest.config import BacktestConfig
from algoverse.backtest.dataview import INCOMPLETE_FILE, load_incomplete
from algoverse.backtest.upstox_gaps import option_rows
from algoverse.backtest.upstox_history import (
    MAX_FAILED_SHARE,
    _cache_dir,
    _finish,
    _load_written,
    _save_written,
    _write_parquet,
)
from algoverse.brokers.base import AuthExpiredError, MarketDataError
from algoverse.data.instruments import nearest_expiries, underlying_key

# Upstox serves about a month of minute candles per request.
CHUNK_DAYS = 28
SESSION_END = pd.Timedelta(hours=15, minutes=30)


@dataclass
class ExpiredFillResult:
    needed: dict[str, int] = field(default_factory=dict)
    requests: int = 0
    written: list[date] = field(default_factory=list)
    extended: list[date] = field(default_factory=list)
    intraday: list[date] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    incomplete: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


def _session_files(data_dir: Path) -> dict[date, Path]:
    files = {}
    for path in history_dir(data_dir).glob("*.parquet"):
        try:
            files[date.fromisoformat(path.stem)] = path
        except ValueError:
            continue
    return files


def _intraday_symbols(data_dir: Path, sessions: list[date]) -> set[str]:
    """Underlyings with 5-minute bars in most sessions, judged from file names."""
    counts: dict[str, int] = defaultdict(int)
    for day in sessions:
        folder = intraday_dir(data_dir) / f"date={day.isoformat()}"
        names = {path.name.split("_")[0].upper() for path in folder.glob("*.parquet")}
        for name in names:
            counts[name] += 1
    return {name for name, count in counts.items() if count * 2 >= len(sessions)}


def plan(
    days: list[date],
    held: dict[date, set[tuple[str, date]]],
    expiries: dict[str, list[date]],
    per_underlying: int,
) -> dict[tuple[str, date], set[date]]:
    """Each missing (underlying, expiry) and the sessions that should hold it."""
    need: dict[tuple[str, date], set[date]] = defaultdict(set)
    for symbol, listed in expiries.items():
        for day in days:
            for expiry in [e for e in listed if e >= day][:per_underlying]:
                if (symbol, expiry) not in held.get(day, set()):
                    need[(symbol, expiry)].add(day)
    return dict(need)


def _band(spot: pd.Series, days: set[date], share: float) -> tuple[float, float] | None:
    window = spot[[day in days for day in spot.index]]
    if window.empty:
        return None
    return float(window.min()) * (1 - share), float(window.max()) * (1 + share)


def _chunks(start: date, end: date):
    first = start
    while first <= end:
        last = min(end, first + timedelta(days=CHUNK_DAYS - 1))
        yield first, last
        first = last + timedelta(days=1)


def _fetch_expiry(
    market,
    symbol: str,
    key: str,
    expiry: date,
    days: set[date],
    spot: pd.Series,
    config: BacktestConfig,
    contracts: pd.DataFrame,
    today: date,
    with_bars: bool,
    result: ExpiredFillResult,
) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """Daily rows, and 5-minute bars when asked, for one expiry over the sessions needing it."""
    band = _band(spot, days, config.strike_range)
    start, end = min(days), max(days)
    if expiry < today:
        listed = [
            {
                "key": row["instrument_key"],
                "strike": float(row["strike_price"]),
                "option_type": row["instrument_type"],
                "lot_size": int(row["lot_size"]),
            }
            for row in market.expired_contracts(key, "option", expiry)
        ]
        result.requests += 1
    else:
        live = contracts[
            (contracts["underlying_symbol"] == symbol) & (contracts["expiry"] == expiry)
        ]
        listed = [
            {
                "key": row.instrument_key,
                "strike": float(row.strike),
                "option_type": row.option_type,
                "lot_size": int(row.lot_size),
            }
            for row in live.itertuples()
        ]
    if band is not None:
        listed = [item for item in listed if band[0] <= item["strike"] <= band[1]]
    daily, bars, failures = [], [], 0
    for item in listed:
        contract = {**item, "expiry": expiry}
        try:
            if with_bars:
                parts = []
                for first, last in _chunks(start, end):
                    result.requests += 1
                    parts.append(
                        market.expired_minute_candles(item["key"], INTRADAY_MINUTES, first, last)
                        if expiry < today
                        else market.minute_range(item["key"], first, last)
                    )
                parts = [part for part in parts if not part.empty]
                if not parts:
                    continue
                candles = _in_session(pd.concat(parts, ignore_index=True))
                if candles.empty:
                    continue
                rows, contract_bars = option_rows(candles, symbol, contract, spot, INTRADAY_MINUTES)
                bars.append(contract_bars)
            else:
                result.requests += 1
                candles = (
                    market.expired_daily_candles(item["key"], start, end)
                    if expiry < today
                    else market.daily_candles(item["key"], start, end)
                )
                if candles.empty:
                    continue
                rows = _daily_rows(candles, symbol, contract, spot)
        except AuthExpiredError:
            raise
        except MarketDataError:
            failures += 1
            continue
        daily.append(rows)
    if failures > MAX_FAILED_SHARE * max(len(listed), 1):
        result.failed[f"{symbol} {expiry}"] = f"{failures} of {len(listed)} contracts failed"
        return None
    daily = [frame for frame in daily if not frame.empty]
    bars = [frame for frame in bars if not frame.empty]
    return (
        pd.concat(daily, ignore_index=True) if daily else pd.DataFrame(columns=COLUMNS),
        pd.concat(bars, ignore_index=True) if bars else pd.DataFrame(),
    )


def _in_session(candles: pd.DataFrame) -> pd.DataFrame:
    """Upstox returns a few candles after the 15:30 close; they are not part of the session."""
    times = pd.to_datetime(candles["ts"])
    if getattr(times.dt, "tz", None) is not None:
        times = times.dt.tz_convert("Asia/Kolkata")
    return candles[(times - times.dt.normalize() < SESSION_END).to_numpy()]


def _daily_rows(candles: pd.DataFrame, symbol: str, contract: dict, spot: pd.Series):
    times = pd.to_datetime(candles["ts"])
    if getattr(times.dt, "tz", None) is not None:
        times = times.dt.tz_convert("Asia/Kolkata")
    days = list(times.dt.date)
    return pd.DataFrame(
        {
            "date": days,
            "symbol": symbol,
            "instrument": "OPT",
            "expiry": contract["expiry"],
            "strike": contract["strike"],
            "option_type": contract["option_type"],
            "open": candles["open"].astype(float).to_numpy(),
            "high": candles["high"].astype(float).to_numpy(),
            "low": candles["low"].astype(float).to_numpy(),
            "close": candles["close"].astype(float).to_numpy(),
            "settle": candles["close"].astype(float).to_numpy(),
            "volume": candles["volume"].astype(float).to_numpy(),
            "oi": candles["oi"].astype(float).to_numpy(),
            "oi_change": candles["oi"].astype(float).diff().fillna(0.0).to_numpy(),
            "lot_size": int(contract["lot_size"]),
            "underlying_price": [float(spot.get(day, float("nan"))) for day in days],
        }
    )


def fill_expired(
    market,
    data_dir: Path,
    config: BacktestConfig,
    contracts: pd.DataFrame,
    index_candles: dict[str, pd.DataFrame],
    today: date,
    note: Callable[[str], None] = lambda _: None,
    dry_run: bool = False,
) -> ExpiredFillResult:
    """Fetch what the history lacks. `index_candles` are each underlying's daily candles."""
    data_dir = Path(data_dir)
    result = ExpiredFillResult()
    files = _session_files(data_dir)
    if not files:
        raise ValueError("No history yet. Import or capture some sessions first.")
    stored = sorted(files)
    end = today - timedelta(days=1)
    index = {symbol: _daily_index(frame) for symbol, frame in index_candles.items()}
    calendar = index.get(config.symbols[0])
    if calendar is None or calendar.empty:
        raise ValueError(f"No daily candles for {config.symbols[0]}. Run backfill first.")
    days = [day for day in calendar.index if stored[0] <= day <= end]

    held: dict[date, set[tuple[str, date]]] = {}
    present: dict[date, set[tuple[str, str]]] = {}
    for day, path in files.items():
        frame = pd.read_parquet(path, columns=["symbol", "instrument", "expiry"])
        options = frame[frame["instrument"] == "OPT"]
        held[day] = set(zip(options["symbol"], options["expiry"], strict=True))
        present[day] = set(zip(frame["symbol"], frame["instrument"], strict=True))

    keys = {symbol: underlying_key(contracts, symbol) for symbol in config.symbols}
    served: dict[str, list[date]] = {}
    for symbol, key in keys.items():
        expired = [e for e in market.expired_expiries(key) if e < today]
        result.requests += 1
        live = nearest_expiries(contracts, symbol, today, None)
        served[symbol] = sorted(set(expired) | set(live))
    need = plan(days, held, served, config.expiries_per_underlying)
    for symbol in config.symbols:
        result.needed[symbol] = sum(1 for s, _ in need if s == symbol)
    if dry_run or not need:
        return result
    with_bars = _intraday_symbols(data_dir, stored)
    note(
        "Missing expiries: "
        + ", ".join(f"{symbol} {count}" for symbol, count in result.needed.items())
    )

    cache = _cache_dir(data_dir) / "expired_gaps"
    fetched_daily: list[pd.DataFrame] = []
    fetched_bars: list[pd.DataFrame] = []
    for number, ((symbol, expiry), wanted) in enumerate(sorted(need.items()), start=1):
        name = f"{symbol}_{expiry.isoformat()}_{min(wanted)}_{max(wanted)}"
        daily_path, bars_path = cache / f"{name}.parquet", cache / f"{name}_bars.parquet"
        if daily_path.exists():
            fetched = (
                pd.read_parquet(daily_path),
                pd.read_parquet(bars_path) if bars_path.exists() else pd.DataFrame(),
            )
        else:
            note(f"[{number}/{len(need)}] {symbol} {expiry}: {len(wanted)} sessions")
            fetched = _fetch_expiry(
                market,
                symbol,
                keys[symbol],
                expiry,
                wanted,
                index[symbol]["close"],
                config,
                contracts,
                today,
                symbol in with_bars,
                result,
            )
            if fetched is None:
                continue
            _write_parquet(daily_path, _finish(fetched[0].copy()))
            if not fetched[1].empty:
                _write_parquet(bars_path, fetched[1])
        daily, bars = fetched
        keep = daily["date"].isin(wanted)
        fetched_daily.append(daily[keep])
        if not bars.empty:
            fetched_bars.append(bars[bars["date"].isin(wanted)])
    if result.failed:
        note("Some expiries failed; they are retried on the next run.")
    if not fetched_daily:
        return result

    added = pd.concat(fetched_daily, ignore_index=True)
    bars = pd.concat(fetched_bars, ignore_index=True) if fetched_bars else pd.DataFrame()
    written = _load_written(data_dir)
    incomplete = load_incomplete(data_dir)
    # Underlyings this run added to a session that did not have them at all.
    new_here: dict[date, set[str]] = defaultdict(set)
    for day, rows in added.groupby("date"):
        path = files.get(day)
        existing = pd.read_parquet(path) if path is not None else pd.DataFrame(columns=COLUMNS)
        have = present.get(day, set())
        index_rows = [
            _index_rows(index[symbol].loc[[day]], symbol)
            for symbol in config.symbols
            if (symbol, "IDX") not in have and symbol in index and day in index[symbol].index
        ]
        before = {symbol for symbol, _ in held.get(day, set())}
        new_here[day] = set(rows["symbol"]) - before
        merged = pd.concat([existing, _finish(rows.copy()), *index_rows], ignore_index=True)
        store_day(data_dir, _finish(merged))
        if path is None:
            written.add(day.isoformat())
            result.written.append(day)
        else:
            result.extended.append(day)
        held[day] = held.get(day, set()) | set(zip(rows["symbol"], rows["expiry"], strict=True))
    _save_written(data_dir, written)

    # Re-judge every session's marks against what it now holds.
    first_served = {symbol: min(listed) for symbol, listed in served.items() if listed}
    for day in days:
        if day not in held:
            continue
        gaps = {}
        for symbol in config.symbols:
            listed = served.get(symbol, [])
            nearest = [e for e in listed if e >= day][: config.expiries_per_underlying]
            lost = [e.isoformat() for e in nearest if (symbol, e) not in held[day]]
            symbol_held = any(s == symbol for s, _ in held[day])
            # Rows added here for a day before the first expiry Upstox serves may lack that
            # day's real nearest expiry. Rows from other sources had their own expiries.
            # The mark is kept on later runs, which no longer know who added the rows.
            earlier = incomplete.get(day.isoformat(), {}).get(symbol, [])
            marked = any(str(item).startswith("any before") for item in earlier)
            added_early = symbol in new_here.get(day, set()) and day < first_served.get(symbol, day)
            if added_early or marked:
                lost.insert(0, f"any before {first_served[symbol].isoformat()}")
            if lost and symbol_held:
                gaps[symbol] = lost
        key = day.isoformat()
        if gaps:
            if incomplete.get(key) != gaps:
                result.incomplete[key] = gaps
            incomplete[key] = gaps
        elif key in incomplete:
            incomplete.pop(key)
            result.completed.append(key)
    path = data_dir / INCOMPLETE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(incomplete, indent=1, sort_keys=True))
    os.replace(tmp, path)

    if not bars.empty:
        for day, part in bars.groupby("date"):
            target = intraday_dir(data_dir) / f"date={day.isoformat()}"
            for symbol, rows in part.groupby("symbol"):
                _write_parquet(
                    target / f"{str(symbol).lower()}_upstox_expired.parquet",
                    rows.drop(columns="date")
                    .sort_values(["expiry", "strike", "option_type", "t"])
                    .reset_index(drop=True),
                )
            result.intraday.append(day)
    result.written.sort()
    result.extended.sort()
    result.intraday.sort()
    return result

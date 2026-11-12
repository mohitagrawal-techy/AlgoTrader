"""Fill missing recent sessions from Upstox, without the Plus plan.

Upstox serves past candles for contracts that are still listed, but not for expired ones
unless the account has Plus. So a missed session can be rebuilt from:

- the index's daily candle,
- futures still trading, from their daily candles,
- options still trading, from their one-minute candles. One request per contract gives both
  the day's row and the 5-minute bars intraday backtests read.

Contracts that expired since the session are missing. The session is written anyway, and the
underlyings whose expiries are missing are recorded in `incomplete.json`. Backtests open no new
trade on those underlyings in those sessions, since "nearest expiry" would mean a different
contract than it did that day, but positions already open are still priced.

Sessions from NSE files or other imports are never replaced. Fetched candles are cached per
underlying, so a run that stops part-way resumes where it left off.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from algoverse.backtest.bhavcopy import COLUMNS, history_dir
from algoverse.backtest.candle_files import (
    INTRADAY_MINUTES,
    _daily_index,
    _index_rows,
    intraday_dir,
)
from algoverse.backtest.config import BacktestConfig
from algoverse.backtest.dataview import INCOMPLETE_FILE, load_incomplete
from algoverse.backtest.upstox_history import (
    MAX_CONTRACT_DAYS,
    MAX_FAILED_SHARE,
    _cache_dir,
    _finish,
    _load_written,
    _save_written,
    _store_owned,
    _strike_band,
    _write_parquet,
)
from algoverse.brokers.base import AuthExpiredError, MarketDataError
from algoverse.data.instruments import nearest_expiries, underlying_key

# Upstox serves at most about a month of one-minute candles per request.
MINUTE_CHUNK_DAYS = 28
SESSION_START = pd.Timedelta(hours=9, minutes=15)


@dataclass
class GapFillResult:
    days: list[date] = field(default_factory=list)
    written: list[date] = field(default_factory=list)
    kept_existing: list[date] = field(default_factory=list)
    intraday: list[date] = field(default_factory=list)
    incomplete: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    contracts: int = 0
    failed: dict[str, str] = field(default_factory=dict)


def _stored_sessions(data_dir: Path) -> list[date]:
    days = []
    for path in history_dir(data_dir).glob("*.parquet"):
        try:
            days.append(date.fromisoformat(path.stem))
        except ValueError:
            continue
    return sorted(days)


def _has_intraday(data_dir: Path, day: date) -> bool:
    return any((intraday_dir(data_dir) / f"date={day.isoformat()}").glob("*.parquet"))


def _expiries_before(data_dir: Path, stored: list[date], day: date, symbol: str) -> set[date]:
    """Option expiries in the last stored session before `day`: what was listed then."""
    earlier = [s for s in stored if s < day]
    if not earlier:
        return set()
    frame = pd.read_parquet(
        history_dir(data_dir) / f"{earlier[-1].isoformat()}.parquet",
        columns=["symbol", "instrument", "expiry"],
    )
    rows = frame[(frame["symbol"] == symbol) & (frame["instrument"] == "OPT")]
    return set(rows["expiry"])


def _minute_range(market, key: str, start: date, end: date) -> pd.DataFrame:
    parts = []
    first = start
    while first <= end:
        last = min(end, first + timedelta(days=MINUTE_CHUNK_DAYS - 1))
        parts.append(market.minute_range(key, first, last))
        first = last + timedelta(days=1)
    parts = [p for p in parts if not p.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _sessions_of(candles: pd.DataFrame) -> pd.Series:
    times = pd.to_datetime(candles["ts"])
    if getattr(times.dt, "tz", None) is None:
        times = times.dt.tz_localize("Asia/Kolkata")
    return times.dt.tz_convert("Asia/Kolkata")


def option_rows(
    minutes: pd.DataFrame, symbol: str, contract: dict, spot: pd.Series, minutes_per_bar: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One contract's daily rows and `minutes_per_bar` bars from its one-minute candles."""
    times = _sessions_of(minutes)
    frame = minutes.assign(local=times, date=times.dt.date)
    frame = frame[frame["close"] > 0]
    if frame.empty:
        return pd.DataFrame(columns=COLUMNS), pd.DataFrame()
    grouped = frame.sort_values("local").groupby("date", sort=True)
    daily = pd.DataFrame(
        {
            "open": grouped["open"].first(),
            "high": grouped["high"].max(),
            "low": grouped["low"].min(),
            "close": grouped["close"].last(),
            "volume": grouped["volume"].sum(),
            "oi": grouped["oi"].last(),
        }
    )
    days = list(daily.index)
    rows = pd.DataFrame(
        {
            "date": days,
            "symbol": symbol,
            "instrument": "OPT",
            "expiry": contract["expiry"],
            "strike": float(contract["strike"]),
            "option_type": contract["option_type"],
            "open": daily["open"].to_numpy(float),
            "high": daily["high"].to_numpy(float),
            "low": daily["low"].to_numpy(float),
            "close": daily["close"].to_numpy(float),
            "settle": daily["close"].to_numpy(float),
            "volume": daily["volume"].to_numpy(float),
            "oi": daily["oi"].to_numpy(float),
            "oi_change": daily["oi"].astype(float).diff().fillna(0.0).to_numpy(),
            "lot_size": int(contract["lot_size"]),
            "underlying_price": [float(spot.get(day, float("nan"))) for day in days],
        }
    )
    # Bars labelled by their start, counted from 09:15 like the imported 5-minute files.
    session_open = frame["local"].dt.normalize() + SESSION_START
    offset = (frame["local"] - session_open) // pd.Timedelta(minutes=minutes_per_bar)
    bar = session_open + offset * pd.Timedelta(minutes=minutes_per_bar)
    keyed = frame.assign(bar=bar).sort_values("local").groupby(["date", "bar"], sort=True)
    bars = pd.DataFrame(
        {
            "open": keyed["open"].first(),
            "high": keyed["high"].max(),
            "low": keyed["low"].min(),
            "close": keyed["close"].last(),
            "volume": keyed["volume"].sum(),
            "oi": keyed["oi"].last(),
        }
    ).reset_index()
    stamps = bars["bar"].dt.tz_convert("UTC")
    bars = pd.DataFrame(
        {
            "date": bars["date"],
            "symbol": symbol,
            "t": ((stamps - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)).astype(
                "int64"
            ),
            "expiry": contract["expiry"],
            "strike": float(contract["strike"]),
            "option_type": contract["option_type"],
            "open": bars["open"].astype(float),
            "high": bars["high"].astype(float),
            "low": bars["low"].astype(float),
            "close": bars["close"].astype(float),
            "volume": bars["volume"].astype(float),
            "oi": bars["oi"].astype(float),
        }
    )
    return rows, bars


def _fetch_symbol(
    market,
    symbol: str,
    config: BacktestConfig,
    contracts: pd.DataFrame,
    futures: pd.DataFrame,
    spot: pd.DataFrame,
    start: date,
    end: date,
    today: date,
    result: GapFillResult,
    note: Callable[[str], None],
) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """Daily rows and 5-minute option bars for one underlying, or None if too much failed."""
    band = _strike_band(spot["close"], start, end, config.strike_range)
    expiries = nearest_expiries(contracts, symbol, today, config.expiries_per_underlying)
    options = contracts[
        (contracts["underlying_symbol"] == symbol) & contracts["expiry"].isin(expiries)
    ]
    if band is not None:
        options = options[(options["strike"] >= band[0]) & (options["strike"] <= band[1])]
    daily, bars, failures = [], [], 0
    total = len(options)
    for number, row in enumerate(options.itertuples(), start=1):
        if number % 100 == 0:
            note(f"{symbol}: {number} of {total} option contracts")
        try:
            minutes = _minute_range(market, row.instrument_key, start, end)
        except AuthExpiredError:
            raise
        except MarketDataError:
            failures += 1
            continue
        if minutes.empty:
            continue
        contract = {
            "expiry": row.expiry,
            "strike": row.strike,
            "option_type": row.option_type,
            "lot_size": row.lot_size,
        }
        rows, contract_bars = option_rows(
            minutes, symbol, contract, spot["close"], INTRADAY_MINUTES
        )
        daily.append(rows)
        bars.append(contract_bars)
    live = futures[(futures["underlying_symbol"] == symbol) & (futures["expiry"] >= today)]
    for row in live.itertuples():
        try:
            candles = market.daily_candles(row.instrument_key, start, end)
        except AuthExpiredError:
            raise
        except MarketDataError:
            failures += 1
            continue
        if candles.empty:
            continue
        days = _sessions_of(candles).dt.date
        daily.append(
            pd.DataFrame(
                {
                    "date": days,
                    "symbol": symbol,
                    "instrument": "FUT",
                    "expiry": row.expiry,
                    "strike": float("nan"),
                    "option_type": None,
                    "open": candles["open"].astype(float),
                    "high": candles["high"].astype(float),
                    "low": candles["low"].astype(float),
                    "close": candles["close"].astype(float),
                    "settle": candles["close"].astype(float),
                    "volume": candles["volume"].astype(float),
                    "oi": candles["oi"].astype(float),
                    "oi_change": candles["oi"].astype(float).diff().fillna(0.0),
                    "lot_size": int(row.lot_size),
                    "underlying_price": [
                        float(spot["close"].get(day, float("nan"))) for day in days
                    ],
                }
            )
        )
    requested = total + len(live)
    if failures > MAX_FAILED_SHARE * max(requested, 1):
        result.failed[symbol] = f"{failures} of {requested} contracts failed"
        return None
    result.contracts += requested - failures
    daily = [d for d in daily if not d.empty]
    bars = [b for b in bars if not b.empty]
    return (
        pd.concat(daily, ignore_index=True) if daily else pd.DataFrame(columns=COLUMNS),
        pd.concat(bars, ignore_index=True) if bars else pd.DataFrame(),
    )


def fill_gaps(
    market,
    data_dir: Path,
    config: BacktestConfig,
    contracts: pd.DataFrame,
    futures: pd.DataFrame,
    today: date,
    note: Callable[[str], None] = lambda _: None,
) -> GapFillResult:
    """Write every recent session the index traded but the history lacks, and the 5-minute
    option bars of recent sessions that have none."""
    data_dir = Path(data_dir)
    result = GapFillResult()
    stored = _stored_sessions(data_dir)
    if not stored:
        raise ValueError("No history yet. Import or capture some sessions first.")
    end = today - timedelta(days=1)
    # Live contracts were listed within about a quarter, so older gaps cannot be rebuilt.
    earliest = max(stored[0], today - timedelta(days=MAX_CONTRACT_DAYS))
    if earliest > end:
        return result
    spots: dict[str, pd.DataFrame] = {}
    for symbol in config.symbols:
        candles = market.daily_candles(underlying_key(contracts, symbol), earliest, end)
        spots[symbol] = _daily_index(candles) if not candles.empty else pd.DataFrame()
    traded = sorted(set().union(*(set(s.index) for s in spots.values() if not s.empty)))
    have = set(stored)
    missing = [d for d in traded if d >= earliest and d not in have]
    # Only sessions saved from Upstox: they hold live contracts alone, so live contracts' bars
    # cover them fully. Older imported sessions also hold contracts that have since expired.
    captured = {date.fromisoformat(day) for day in _load_written(data_dir)}
    no_bars = [d for d in traded if d in have and d in captured and not _has_intraday(data_dir, d)]
    result.days = missing
    targets = sorted(set(missing) | set(no_bars))
    if not targets:
        return result
    start, stop = targets[0], targets[-1]
    note(
        f"{len(missing)} missing sessions and {len(no_bars)} without 5-minute bars, "
        f"{start} to {stop}"
    )

    cache = _cache_dir(data_dir) / "gaps" / f"{start}_{stop}"
    daily_parts, bar_parts = [], []
    for symbol in config.symbols:
        if spots[symbol].empty:
            result.failed[symbol] = "Upstox returned no index candles"
            continue
        daily_path, bars_path = cache / f"{symbol}.parquet", cache / f"{symbol}_bars.parquet"
        if daily_path.exists() and bars_path.exists():
            note(f"{symbol}: using candles fetched earlier")
            fetched = (pd.read_parquet(daily_path), pd.read_parquet(bars_path))
        else:
            note(f"{symbol}: fetching contracts")
            fetched = _fetch_symbol(
                market,
                symbol,
                config,
                contracts,
                futures,
                spots[symbol],
                start,
                stop,
                today,
                result,
                note,
            )
            if fetched is None:
                continue
            _write_parquet(daily_path, _finish(fetched[0].copy()))
            _write_parquet(bars_path, fetched[1])
        daily_parts.append(fetched[0])
        daily_parts.append(_index_rows(spots[symbol], symbol))
        bar_parts.append(fetched[1])
    if result.failed:
        # A session missing an underlying would look like a day it did not trade.
        return result

    daily = pd.concat([d for d in daily_parts if not d.empty], ignore_index=True)
    written = _load_written(data_dir)
    incomplete = load_incomplete(data_dir)
    for day in missing:
        group = daily[daily["date"] == day]
        if group.empty:
            continue
        if _store_owned(data_dir, _finish(group.copy()), written) is None:
            result.kept_existing.append(day)
            continue
        result.written.append(day)
        gaps = {}
        for symbol in config.symbols:
            fetched = set(
                group.loc[(group["symbol"] == symbol) & (group["instrument"] == "OPT"), "expiry"]
            )
            listed = _expiries_before(data_dir, stored, day, symbol)
            lost = sorted(e for e in listed if day <= e < today and e not in fetched)
            if lost:
                gaps[symbol] = [e.isoformat() for e in lost]
        if gaps:
            incomplete[day.isoformat()] = gaps
            result.incomplete[day.isoformat()] = gaps
        else:
            incomplete.pop(day.isoformat(), None)
    _save_written(data_dir, written)
    path = data_dir / INCOMPLETE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(incomplete, indent=1, sort_keys=True))
    os.replace(tmp, path)

    bars = pd.concat([b for b in bar_parts if not b.empty], ignore_index=True)
    for day in sorted(set(result.written) | set(no_bars)):
        if _has_intraday(data_dir, day):
            continue
        session = pd.read_parquet(
            history_dir(data_dir) / f"{day.isoformat()}.parquet",
            columns=["symbol", "instrument", "expiry", "strike", "option_type"],
        )
        known = session[session["instrument"] == "OPT"].drop(columns="instrument")
        day_bars = bars[bars["date"] == day].merge(
            known, on=["symbol", "expiry", "strike", "option_type"], how="inner"
        )
        if day_bars.empty:
            continue
        folder = intraday_dir(data_dir) / f"date={day.isoformat()}"
        for symbol, part in day_bars.groupby("symbol"):
            _write_parquet(
                folder / f"{str(symbol).lower()}_upstox.parquet",
                part.drop(columns="date")
                .sort_values(["expiry", "strike", "option_type", "t"])
                .reset_index(drop=True),
            )
        result.intraday.append(day)
    return result

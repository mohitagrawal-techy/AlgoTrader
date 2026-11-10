"""Fills the backtest store from Upstox, in the same daily format as NSE files.

Two routes write sessions:
- `import_expired` reads past contracts and needs the Upstox Plus plan.
- `capture_session` saves today's contracts after the close and needs no extra plan.

A backtest on a session with missing expiries would pick the wrong contracts, so only sessions
known to hold every live expiry are written. Sessions imported from NSE files are never replaced.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from algoverse.backtest.bhavcopy import COLUMNS, history_dir, store_day
from algoverse.backtest.config import BacktestConfig
from algoverse.brokers.base import AuthExpiredError, MarketDataError, PlanRequiredError
from algoverse.data.instruments import nearest_expiries, underlying_key

IST = ZoneInfo("Asia/Kolkata")
# Replays use the nearest few expiries, which are all listed within about one quarter.
MAX_CONTRACT_DAYS = 95
MAX_FAILED_SHARE = 0.02
MIN_QUOTED_SHARE = 0.8


class CaptureRefused(ValueError):
    """Today's session cannot be saved. The message says why."""


@dataclass
class ExpiredImportResult:
    contracts: int = 0
    written: list[date] = field(default_factory=list)
    kept_existing: list[date] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


# ---- Storage -------------------------------------------------------------------------------


def _cache_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "history" / "upstox"


def _load_written(data_dir: Path) -> set[str]:
    path = _cache_dir(data_dir) / "written.json"
    return set(json.loads(path.read_text())) if path.exists() else set()


def _save_written(data_dir: Path, written: set[str]) -> None:
    path = _cache_dir(data_dir) / "written.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(written)))
    os.replace(tmp, path)


def _store_owned(data_dir: Path, frame: pd.DataFrame, written: set[str]) -> Path | None:
    """Write a session unless a file from another source already holds that date."""
    day = frame["date"].iloc[0].isoformat()
    if (history_dir(data_dir) / f"{day}.parquet").exists() and day not in written:
        return None
    written.add(day)
    return store_day(data_dir, frame)


def _write_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def _identity(frame: pd.DataFrame) -> pd.Series:
    """One label per contract. Futures have no strike or option type."""
    return (
        frame["symbol"]
        + "|"
        + frame["instrument"]
        + "|"
        + frame["expiry"].astype(str)
        + "|"
        + frame["strike"].fillna(0.0).map("{:g}".format)
        + "|"
        + frame["option_type"].fillna("")
    )


def _finish(frame: pd.DataFrame) -> pd.DataFrame:
    frame["option_type"] = frame["option_type"].astype(object)
    return frame[COLUMNS].reset_index(drop=True)


# ---- Daily capture -------------------------------------------------------------------------


def _trade_date(quote: dict) -> date | None:
    raw = quote.get("last_trade_time")
    if not raw or int(raw) <= 0:
        return None
    return datetime.fromtimestamp(int(raw) / 1000, IST).date()


def _quote_row(
    quote: dict,
    session: date,
    symbol: str,
    instrument: str,
    expiry: date,
    strike: float,
    option_type: str | None,
    lot_size: int,
    spot: float,
) -> dict:
    # A contract that did not trade today still reports its last price and old day figures.
    ohlc = (quote.get("ohlc") or {}) if _trade_date(quote) == session else {}
    close = float(quote.get("last_price") or 0.0)
    return {
        "date": session,
        "symbol": symbol,
        "instrument": instrument,
        "expiry": expiry,
        "strike": strike,
        "option_type": option_type,
        "open": float(ohlc.get("open") or 0.0),
        "high": float(ohlc.get("high") or 0.0),
        "low": float(ohlc.get("low") or 0.0),
        "close": close,
        "settle": close,
        "volume": float(quote.get("volume") or 0.0) if ohlc else 0.0,
        "oi": float(quote.get("oi") or 0.0),
        "oi_change": 0.0,
        "lot_size": int(lot_size),
        "underlying_price": spot,
    }


def _check_share(found: int, requested: int, label: str) -> None:
    if requested == 0 or found / requested < MIN_QUOTED_SHARE:
        raise CaptureRefused(f"{label}: Upstox returned {found} of {requested} contracts")


def _capture_symbol(
    market,
    symbol: str,
    spot_quote: dict,
    config: BacktestConfig,
    contracts: pd.DataFrame,
    futures: pd.DataFrame,
    session: date,
) -> list[dict]:
    if _trade_date(spot_quote) != session:
        raise CaptureRefused(f"{symbol} did not trade on {session}. The market looks closed.")
    spot = float(spot_quote["last_price"])
    low, high = spot * (1 - config.strike_range), spot * (1 + config.strike_range)
    expiries = nearest_expiries(contracts, symbol, session, config.expiries_per_underlying)
    if not expiries:
        raise CaptureRefused(f"{symbol}: no live option expiries in the contract master")
    options = contracts[
        (contracts["underlying_symbol"] == symbol)
        & contracts["expiry"].isin(expiries)
        & contracts["strike"].between(low, high)
    ]
    wanted = futures[(futures["underlying_symbol"] == symbol) & (futures["expiry"] >= session)]
    if wanted.empty:
        raise CaptureRefused(f"{symbol}: no live future in the contract master")
    keys = list(options["instrument_key"]) + list(wanted["instrument_key"])
    quotes = market.quotes(keys)
    rows = []
    for expiry, group in options.groupby("expiry"):
        found = [row for row in group.itertuples() if row.instrument_key in quotes]
        _check_share(len(found), len(group), f"{symbol} {expiry} options")
        rows += [
            _quote_row(
                quotes[row.instrument_key],
                session,
                symbol,
                "OPT",
                expiry,
                float(row.strike),
                row.option_type,
                row.lot_size,
                spot,
            )
            for row in found
        ]
    nearest = wanted.sort_values("expiry").iloc[0]
    if nearest["instrument_key"] not in quotes:
        raise CaptureRefused(f"{symbol}: Upstox returned no quote for the nearest future")
    rows += [
        _quote_row(
            quotes[row.instrument_key],
            session,
            symbol,
            "FUT",
            row.expiry,
            float("nan"),
            None,
            row.lot_size,
            spot,
        )
        for row in wanted.itertuples()
        if row.instrument_key in quotes
    ]
    return rows


def _latest_before(data_dir: Path, session: date) -> pd.DataFrame | None:
    earlier = sorted(p for p in history_dir(data_dir).glob("*.parquet") if p.stem < str(session))
    return pd.read_parquet(earlier[-1], columns=COLUMNS) if earlier else None


def build_capture(
    market,
    config: BacktestConfig,
    contracts: pd.DataFrame,
    futures: pd.DataFrame,
    session: date,
    previous: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One session from closing quotes. Refuses a closed market or a partial chain."""
    keys = {symbol: underlying_key(contracts, symbol) for symbol in config.symbols}
    spots = market.quotes(list(keys.values()))
    rows = []
    for symbol, key in keys.items():
        if key not in spots:
            raise CaptureRefused(f"{symbol}: Upstox returned no index quote")
        rows += _capture_symbol(market, symbol, spots[key], config, contracts, futures, session)
    frame = pd.DataFrame(rows)
    frame = frame[frame["close"] > 0].reset_index(drop=True)
    if previous is not None and not previous.empty:
        before = dict(zip(_identity(previous), previous["oi"].to_numpy(float), strict=True))
        prior = _identity(frame).map(before)
        frame["oi_change"] = (frame["oi"] - prior).fillna(0.0)
    return _finish(frame)


def capture_session(
    market,
    data_dir: Path,
    config: BacktestConfig,
    contracts: pd.DataFrame,
    futures: pd.DataFrame,
    session: date,
) -> Path:
    frame = build_capture(
        market, config, contracts, futures, session, _latest_before(data_dir, session)
    )
    written = _load_written(data_dir)
    path = _store_owned(data_dir, frame, written)
    if path is None:
        raise CaptureRefused(f"{session} is already imported from NSE files")
    _save_written(data_dir, written)
    return path


# ---- Expired contracts ---------------------------------------------------------------------


def _contract_rows(
    candles: pd.DataFrame,
    symbol: str,
    instrument: str,
    expiry: date,
    strike: float,
    option_type: str | None,
    lot_size: int,
    spot: pd.Series,
) -> pd.DataFrame:
    days = pd.to_datetime(candles["ts"]).dt.date
    frame = pd.DataFrame(
        {
            "date": days,
            "symbol": symbol,
            "instrument": instrument,
            "expiry": expiry,
            "strike": strike,
            "option_type": option_type,
            "open": candles["open"].astype(float),
            "high": candles["high"].astype(float),
            "low": candles["low"].astype(float),
            "close": candles["close"].astype(float),
            "settle": candles["close"].astype(float),
            "volume": candles["volume"].astype(float),
            "oi": candles["oi"].astype(float),
            "oi_change": candles["oi"].astype(float).diff().fillna(0.0),
            "lot_size": int(lot_size),
            "underlying_price": days.map(spot).astype(float),
        }
    )
    return frame[frame["date"] <= expiry]


def _strike_band(
    spot: pd.Series, start: date, end: date, strike_range: float
) -> tuple[float, float] | None:
    window = spot[[start <= day <= end for day in spot.index]]
    if window.empty:
        return None
    return float(window.min()) * (1 - strike_range), float(window.max()) * (1 + strike_range)


def _in_band(strike: float, band: tuple[float, float] | None) -> bool:
    return band is None or band[0] <= strike <= band[1]


def _fetch_group(
    fetch: Callable[[str, date, date], pd.DataFrame],
    requests: list[dict],
    start: date,
    end: date,
    symbol: str,
    spot: pd.Series,
    result: ExpiredImportResult,
    label: str,
) -> pd.DataFrame | None:
    """Candles for a group of contracts, or None when too many of them failed."""
    frames, failures = [], 0
    for item in requests:
        try:
            candles = fetch(item["key"], start, end)
        except (AuthExpiredError, PlanRequiredError):
            raise
        except MarketDataError:
            failures += 1
            continue
        if not candles.empty:
            frames.append(
                _contract_rows(
                    candles,
                    symbol,
                    item["instrument"],
                    item["expiry"],
                    item["strike"],
                    item["option_type"],
                    item["lot_size"],
                    spot,
                )
            )
    if failures > MAX_FAILED_SHARE * len(requests):
        result.failed[label] = f"{failures} of {len(requests)} contracts failed"
        return None
    result.contracts += len(requests) - failures
    frames = [frame for frame in frames if not frame.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)


def _import_expiry(
    market,
    key: str,
    symbol: str,
    expiry: date,
    spot: pd.Series,
    config: BacktestConfig,
    result: ExpiredImportResult,
) -> pd.DataFrame | None:
    start = expiry - timedelta(days=MAX_CONTRACT_DAYS)
    band = _strike_band(spot, start, expiry, config.strike_range)
    requests = [
        {
            "key": row["instrument_key"],
            "instrument": "OPT",
            "expiry": expiry,
            "strike": float(row["strike_price"]),
            "option_type": row["instrument_type"],
            "lot_size": row["lot_size"],
        }
        for row in market.expired_contracts(key, "option", expiry)
        if _in_band(float(row["strike_price"]), band)
    ]
    requests += [
        {
            "key": row["instrument_key"],
            "instrument": "FUT",
            "expiry": expiry,
            "strike": float("nan"),
            "option_type": None,
            "lot_size": row["lot_size"],
        }
        for row in market.expired_contracts(key, "future", expiry)
    ]
    return _fetch_group(
        market.expired_daily_candles,
        requests,
        start,
        expiry,
        symbol,
        spot,
        result,
        f"{symbol} {expiry}",
    )


def _import_active(
    market,
    symbol: str,
    spot: pd.Series,
    config: BacktestConfig,
    contracts: pd.DataFrame,
    futures: pd.DataFrame,
    today: date,
    end: date,
    result: ExpiredImportResult,
) -> pd.DataFrame | None:
    """Live contracts, which recent sessions need alongside the expired ones."""
    start = today - timedelta(days=MAX_CONTRACT_DAYS)
    band = _strike_band(spot, start, end, config.strike_range)
    expiries = nearest_expiries(contracts, symbol, today, config.expiries_per_underlying)
    options = contracts[
        (contracts["underlying_symbol"] == symbol) & contracts["expiry"].isin(expiries)
    ]
    requests = [
        {
            "key": row.instrument_key,
            "instrument": "OPT",
            "expiry": row.expiry,
            "strike": float(row.strike),
            "option_type": row.option_type,
            "lot_size": row.lot_size,
        }
        for row in options.itertuples()
        if _in_band(float(row.strike), band)
    ]
    live_futures = futures[(futures["underlying_symbol"] == symbol) & (futures["expiry"] >= today)]
    requests += [
        {
            "key": row.instrument_key,
            "instrument": "FUT",
            "expiry": row.expiry,
            "strike": float("nan"),
            "option_type": None,
            "lot_size": row.lot_size,
        }
        for row in live_futures.itertuples()
    ]
    return _fetch_group(
        market.daily_candles, requests, start, end, symbol, spot, result, f"{symbol} live"
    )


def _first_covered(cache: Path, expiries: list[date]) -> date | None:
    """The earliest expiry from which every later expiry has been fetched."""
    first = None
    for expiry in reversed(expiries):
        if not (cache / f"{expiry}.parquet").exists():
            break
        first = expiry
    return first


def _build_sessions(
    data_dir: Path,
    symbols: list[str],
    start: date,
    end: date,
    result: ExpiredImportResult,
) -> None:
    cache = _cache_dir(data_dir)
    # Expired files come first, so they win over the same contract from an older live fetch.
    files = [p for s in symbols for p in sorted((cache / s).glob("*.parquet"))]
    files += [p for s in symbols for p in sorted((cache / s / "active").glob("*.parquet"))]
    frame = pd.concat([pd.read_parquet(path, columns=COLUMNS) for path in files], ignore_index=True)
    frame = frame[(frame["date"] >= start) & (frame["date"] <= end)]
    frame = frame[~frame.assign(identity=_identity(frame)).duplicated(["date", "identity"])]
    written = _load_written(data_dir)
    for day, group in frame.groupby("date"):
        if set(symbols) - set(group["symbol"]):
            continue
        stored = _store_owned(data_dir, _finish(group.copy()), written)
        (result.written if stored else result.kept_existing).append(day)
    _save_written(data_dir, written)
    result.written.sort()
    result.kept_existing.sort()


def import_expired(
    market,
    data_dir: Path,
    config: BacktestConfig,
    contracts: pd.DataFrame,
    futures: pd.DataFrame,
    today: date,
    note: Callable[[str], None] = lambda _: None,
) -> ExpiredImportResult:
    """Fetch every expiry Upstox still serves. Finished expiries are skipped on a later run."""
    result = ExpiredImportResult()
    cache = _cache_dir(data_dir)
    end = today - timedelta(days=1)
    expiries = {}
    for symbol in config.symbols:
        key = underlying_key(contracts, symbol)
        # Asked first, so an account without the plan stops before any other request.
        expiries[symbol] = (key, [e for e in market.expired_expiries(key) if e <= end])
    coverage: list[date] = []
    for symbol, (key, dates) in expiries.items():
        if not dates:
            result.failed[symbol] = "Upstox listed no expired contracts"
            continue
        earliest = dates[0] - timedelta(days=MAX_CONTRACT_DAYS)
        candles = market.daily_candles(key, earliest, end)
        spot = pd.Series(
            candles["close"].astype(float).to_numpy(),
            index=pd.to_datetime(candles["ts"]).dt.date,
        )
        folder = cache / symbol
        for expiry in dates:
            path = folder / f"{expiry}.parquet"
            if path.exists():
                continue
            note(f"{symbol} {expiry}: fetching contracts")
            frame = _import_expiry(market, key, symbol, expiry, spot, config, result)
            if frame is not None:
                _write_parquet(path, _finish(frame))
        note(f"{symbol}: fetching live contracts")
        live = _import_active(market, symbol, spot, config, contracts, futures, today, end, result)
        if live is None:
            continue  # Recent sessions would lack their nearest expiries.
        _write_parquet(folder / "active" / f"{today}.parquet", _finish(live))
        for old in (folder / "active").glob("*.parquet"):
            if old.stem != today.isoformat():
                old.unlink()
        covered = _first_covered(folder, dates)
        if covered is not None:
            coverage.append(covered)
    if len(coverage) == len(config.symbols):
        _build_sessions(data_dir, list(config.symbols), max(coverage), end, result)
    return result

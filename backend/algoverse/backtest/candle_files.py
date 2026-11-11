"""Builds backtest sessions from one-minute option candles saved as files.

The folder holds what an Upstox expired-contract download leaves behind:

    candles/1minute/<expiry>/<strike>_<CE|PE>.csv   timestamp, open, high, low, close, volume,
                                                    open_interest (one row a minute)
    expired_contracts/<expiry>.csv or .json         the contract list, with lot sizes

Each contract's minutes become one daily bar per session. A session is written only when it
holds every expiry a backtest could pick (the nearest few), so a replay never trades a contract
that was missing from the data rather than from the market. The index's own daily bar is added
to each session, because these files have no futures to take daily bars from.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from algoverse.backtest.bhavcopy import COLUMNS, history_dir
from algoverse.backtest.upstox_history import _load_written, _save_written, _store_owned

log = logging.getLogger(__name__)
INDEX = "IDX"
MINUTES_DIR = Path("candles") / "1minute"
CONTRACTS_DIR = "expired_contracts"
# An expiry counts as present on a session when at least this many of its contracts traded.
MIN_CONTRACTS = 10
# Upstox reuses exchange numbers, so a file can carry stray bars of an older contract. Only
# the run of sessions ending at expiry is kept: no gap longer than this, within MAX_LIFE_DAYS.
MAX_GAP_DAYS = 20
MAX_LIFE_DAYS = 95


@dataclass
class FileImportResult:
    expiries: int = 0
    contracts: int = 0
    written: list[date] = field(default_factory=list)
    kept_existing: list[date] = field(default_factory=list)
    incomplete: list[date] = field(default_factory=list)
    problems: dict[str, str] = field(default_factory=dict)


def _contract_list(folder: Path, expiry: str) -> pd.DataFrame:
    """Strike, type and lot size for each contract of one expiry."""
    csv_path = folder / CONTRACTS_DIR / f"{expiry}.csv"
    json_path = folder / CONTRACTS_DIR / f"{expiry}.json"
    if csv_path.exists():
        raw = pd.read_csv(csv_path)
    elif json_path.exists():
        payload = json.loads(json_path.read_text())
        raw = pd.DataFrame(payload.get("data", payload) if isinstance(payload, dict) else payload)
    else:
        return pd.DataFrame(columns=["strike", "option_type", "lot_size", "symbol"])
    return pd.DataFrame(
        {
            "strike": pd.to_numeric(raw["strike_price"], errors="coerce").astype(float),
            "option_type": raw["instrument_type"].astype(str),
            "lot_size": pd.to_numeric(raw["lot_size"], errors="coerce"),
            "symbol": raw["underlying_symbol"].astype(str),
        }
    ).dropna(subset=["strike", "lot_size"])


def daily_bars(expiry_folder: Path) -> pd.DataFrame:
    """One bar per contract per session from every minute file of one expiry."""
    query = """
        WITH minutes AS (
            SELECT regexp_extract(filename, '([0-9.]+)_(CE|PE)\\.csv$', 1)::DOUBLE AS strike,
                   regexp_extract(filename, '([0-9.]+)_(CE|PE)\\.csv$', 2) AS option_type,
                   CAST(timestamp AS TIMESTAMPTZ) AS ts,
                   open, high, low, close, volume, open_interest
            FROM read_csv(?, filename = true, union_by_name = true,
                          columns = {'timestamp': 'VARCHAR', 'open': 'DOUBLE',
                                     'high': 'DOUBLE', 'low': 'DOUBLE', 'close': 'DOUBLE',
                                     'volume': 'DOUBLE', 'open_interest': 'DOUBLE'})
        )
        SELECT strike, option_type,
               CAST(timezone('Asia/Kolkata', ts) AS DATE) AS date,
               arg_min(open, ts) AS open, max(high) AS high, min(low) AS low,
               arg_max(close, ts) AS close, sum(volume) AS volume,
               arg_max(open_interest, ts) AS oi
        FROM minutes
        WHERE close > 0
        GROUP BY ALL
        ORDER BY strike, option_type, date
    """
    with duckdb.connect() as connection:
        return connection.execute(query, [str(expiry_folder / "*.csv")]).df()


def _own_sessions(bars: pd.DataFrame, expiry: date) -> pd.DataFrame:
    """Each contract's bars from its last continuous run up to expiry."""
    bars = bars[
        (bars["date"] <= expiry) & (bars["date"] >= expiry - pd.Timedelta(days=MAX_LIFE_DAYS))
    ]
    bars = bars.sort_values(["strike", "option_type", "date"])
    gap = bars.groupby(["strike", "option_type"])["date"].diff()
    run = (
        (gap > pd.Timedelta(days=MAX_GAP_DAYS))
        .groupby([bars["strike"], bars["option_type"]])
        .cumsum()
    )
    last_run = run.groupby([bars["strike"], bars["option_type"]]).transform("max")
    return bars[run == last_run].reset_index(drop=True)


def _index_rows(spot: pd.DataFrame, symbol: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": spot.index,
            "symbol": symbol,
            "instrument": INDEX,
            "expiry": spot.index,
            "strike": float("nan"),
            "option_type": None,
            "open": spot["open"].to_numpy(float),
            "high": spot["high"].to_numpy(float),
            "low": spot["low"].to_numpy(float),
            "close": spot["close"].to_numpy(float),
            "settle": spot["close"].to_numpy(float),
            "volume": 0.0,
            "oi": 0.0,
            "oi_change": 0.0,
            "lot_size": 0,
            "underlying_price": spot["close"].to_numpy(float),
        }
    )


def _daily_index(candles: pd.DataFrame) -> pd.DataFrame:
    """Index OHLC by session date."""
    times = pd.to_datetime(candles["ts"])
    if getattr(times.dt, "tz", None) is not None:
        times = times.dt.tz_convert("Asia/Kolkata")
    frame = candles.assign(date=times.dt.date).drop_duplicates("date", keep="last")
    return frame.set_index("date")[["open", "high", "low", "close"]].astype(float)


def import_candle_files(
    folder: Path,
    data_dir: Path,
    symbol: str,
    index_candles: Callable[[str], pd.DataFrame],
    expiries_needed: int,
    strike_range: float,
    progress: Callable[[str], None] = lambda _message: None,
    dry_run: bool = False,
) -> FileImportResult:
    folder = Path(folder)
    result = FileImportResult()
    root = folder / MINUTES_DIR
    if not root.is_dir():
        raise ValueError(f"No {MINUTES_DIR} folder in {folder}")
    spot = _daily_index(index_candles(symbol))
    if spot.empty:
        raise ValueError(f"No daily {symbol} index candles. Run `algoverse backfill` first.")

    frames = []
    expiry_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    for number, expiry_dir in enumerate(expiry_dirs, start=1):
        try:
            expiry = date.fromisoformat(expiry_dir.name)
        except ValueError:
            continue
        contracts = _contract_list(folder, expiry_dir.name)
        if not contracts.empty and set(contracts["symbol"]) - {symbol}:
            result.problems[expiry_dir.name] = f"contracts are not {symbol}"
            continue
        bars = daily_bars(expiry_dir)
        if bars.empty:
            result.problems[expiry_dir.name] = "no candles"
            continue
        lots = contracts.drop_duplicates(["strike", "option_type"]).set_index(
            ["strike", "option_type"]
        )["lot_size"]
        bars["lot_size"] = [
            lots.get((s, t)) for s, t in zip(bars["strike"], bars["option_type"], strict=True)
        ]
        if bars["lot_size"].isna().all():
            # Without a contract list, the lot size in force is unknown.
            result.problems[expiry_dir.name] = "no contract list with lot sizes"
            continue
        bars["lot_size"] = bars["lot_size"].fillna(bars["lot_size"].mode().iloc[0])
        bars["date"] = pd.to_datetime(bars["date"]).dt.date
        bars = _own_sessions(bars, expiry)
        bars["expiry"] = expiry
        frames.append(bars)
        result.expiries += 1
        result.contracts += len(bars.drop_duplicates(["strike", "option_type"]))
        progress(f"[{number}/{len(expiry_dirs)}] {expiry}: {len(bars)} daily bars")

    if not frames:
        return result
    options = pd.concat(frames, ignore_index=True)
    options = options[options["date"].isin(spot.index)]
    options["underlying_price"] = options["date"].map(spot["close"])
    band = (options["strike"] / options["underlying_price"] - 1).abs() <= strike_range
    options = options[band].sort_values(["expiry", "strike", "option_type", "date"])
    options["oi_change"] = (
        options.groupby(["expiry", "strike", "option_type"])["oi"].diff().fillna(0.0)
    )
    options = options.assign(symbol=symbol, instrument="OPT", settle=options["close"]).reset_index(
        drop=True
    )

    written = _load_written(data_dir)
    all_expiries = sorted(options["expiry"].unique())
    for session, day in options.groupby("date"):
        listed = [e for e in all_expiries if e >= session][:expiries_needed]
        counts = day.groupby("expiry").size()
        complete = len(listed) == expiries_needed and all(
            counts.get(e, 0) >= MIN_CONTRACTS for e in listed
        )
        if not complete:
            result.incomplete.append(session)
            continue
        frame = pd.concat([day, _index_rows(spot.loc[[session]], symbol)], ignore_index=True)
        frame["option_type"] = frame["option_type"].astype(object)
        frame = frame[COLUMNS].reset_index(drop=True)
        if dry_run:
            result.written.append(session)
            continue
        existing = history_dir(data_dir) / f"{session.isoformat()}.parquet"
        if existing.exists() and session.isoformat() in written:
            # Another Upstox capture of this session may hold other underlyings: keep them.
            other = pd.read_parquet(existing)
            frame = pd.concat([other[other["symbol"] != symbol], frame], ignore_index=True)
        if _store_owned(data_dir, frame, written) is None:
            result.kept_existing.append(session)
            continue
        result.written.append(session)
    if not dry_run:
        _save_written(data_dir, written)
    return result


# ---- Intraday option bars ------------------------------------------------------------------

INTRADAY_DIR = "intraday"
INTRADAY_MINUTES = 5


def intraday_dir(data_dir: Path) -> Path:
    return history_dir(data_dir) / INTRADAY_DIR


@dataclass
class IntradayImportResult:
    expiries: int = 0
    rows: int = 0
    sessions: int = 0


def import_intraday(
    folder: Path,
    data_dir: Path,
    symbol: str,
    minutes: int = INTRADAY_MINUTES,
    progress: Callable[[str], None] = lambda _message: None,
) -> IntradayImportResult:
    """Write `minutes`-minute option bars per session under history/intraday/date=<day>/.

    Only contracts already in that session's daily history are kept, so the same stray-bar,
    strike-band and complete-expiry rules apply. Each bar is labelled by its start time; its
    close is known at the start plus `minutes`.
    """
    folder, data_dir = Path(folder), Path(data_dir)
    root = folder / MINUTES_DIR
    daily_glob = str(history_dir(data_dir) / "*.parquet")
    if not any(history_dir(data_dir).glob("*.parquet")):
        raise ValueError("Import the daily sessions first (algoverse history-files).")
    staging = history_dir(data_dir) / f".{INTRADAY_DIR}-staging"
    staging.mkdir(parents=True, exist_ok=True)
    result = IntradayImportResult()
    query = f"""
        COPY (
            WITH minutes AS (
                SELECT regexp_extract(filename, '([0-9.]+)_(CE|PE)\\.csv$', 1)::DOUBLE AS strike,
                       regexp_extract(filename, '([0-9.]+)_(CE|PE)\\.csv$', 2) AS option_type,
                       CAST(timestamp AS TIMESTAMPTZ) AS ts,
                       open, high, low, close, volume, open_interest
                FROM read_csv(?, filename = true,
                              columns = {{'timestamp': 'VARCHAR', 'open': 'DOUBLE',
                                         'high': 'DOUBLE', 'low': 'DOUBLE', 'close': 'DOUBLE',
                                         'volume': 'DOUBLE', 'open_interest': 'DOUBLE'}})
                WHERE close > 0
            ),
            keyed AS (
                SELECT *, CAST(timezone('Asia/Kolkata', ts) AS DATE) AS date,
                       time_bucket(INTERVAL '{int(minutes)} minutes', ts,
                                   TIMESTAMPTZ '2000-01-03 09:15:00+05:30') AS bar
                FROM minutes
            ),
            wanted AS (
                SELECT DISTINCT date, strike, option_type FROM read_parquet(?)
                WHERE instrument = 'OPT' AND symbol = ? AND expiry = ?
            )
            SELECT k.date, epoch_ms(k.bar) AS t, ?::DATE AS expiry, k.strike, k.option_type,
                   arg_min(k.open, k.ts) AS open, max(k.high) AS high, min(k.low) AS low,
                   arg_max(k.close, k.ts) AS close, sum(k.volume) AS volume,
                   arg_max(k.open_interest, k.ts) AS oi
            FROM keyed k JOIN wanted w USING (date, strike, option_type)
            GROUP BY ALL
        ) TO ? (FORMAT parquet, COMPRESSION zstd)
    """
    expiry_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    with duckdb.connect() as connection:
        # Large writes spill to disk; keep that inside the data folder, which is writable.
        connection.execute("SET temp_directory = ?", [str(staging / "spill")])
        for number, expiry_dir in enumerate(expiry_dirs, start=1):
            try:
                expiry = date.fromisoformat(expiry_dir.name)
            except ValueError:
                continue
            target = staging / f"{expiry_dir.name}.parquet"
            connection.execute(
                query,
                [str(expiry_dir / "*.csv"), daily_glob, symbol, expiry, expiry, str(target)],
            )
            rows = connection.execute(
                "SELECT count(*) FROM read_parquet(?)", [str(target)]
            ).fetchone()[0]
            if rows:
                result.expiries += 1
                result.rows += rows
            else:
                target.unlink()
            progress(f"[{number}/{len(expiry_dirs)}] {expiry}: {rows} bars")
        out = intraday_dir(data_dir)
        out.mkdir(parents=True, exist_ok=True)
        if any(staging.glob("*.parquet")):
            connection.execute(
                f"""
                COPY (
                    SELECT ? AS symbol, * FROM read_parquet(?)
                    ORDER BY date, expiry, strike, option_type, t
                ) TO ? (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (date),
                        OVERWRITE_OR_IGNORE true, FILENAME_PATTERN '{symbol.lower()}_{{i}}')
                """,
                [symbol, str(staging / "*.parquet"), str(out)],
            )
    result.sessions = len(list(intraday_dir(data_dir).glob("date=*")))
    shutil.rmtree(staging, ignore_errors=True)
    return result

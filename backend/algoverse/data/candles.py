"""Price history on disk, laid out so new markets and intervals need no changes.

    candles/interval=<1d|1m|5m|...>/segment=<NSE_INDEX|NSE_EQ|NSE_FO|MCX_FO|...>/symbol=<name>/
        year=<YYYY>/part.parquet        daily and slower intervals
        month=<YYYY-MM>/part.parquet    intraday intervals
    series/name=<series>/symbol=<symbol>/year=<YYYY>/part.parquet
                                        derived daily series, such as ATM implied volatility

Candles are keyed by the Upstox instrument key, which already names the exchange segment, so
indices, stocks, futures and commodities sit side by side. One instrument has one folder, so the
same index is never stored twice under two names. A day's update rewrites one small file, and
finished years or months never change, which keeps the S3 copy cheap to refresh.

Readers can use the whole tree as one Hive-partitioned dataset, for example in DuckDB:
    read_parquet('candles/*/*/*/*/part.parquet', hive_partitioning = true)
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
CANDLES = "candles"
SERIES = "series"
FILE = "part.parquet"
DAILY = "1d"
MINUTE = "1m"
# How each interval is split into files.
INTERVALS = {
    "1m": "month",
    "3m": "month",
    "5m": "month",
    "15m": "month",
    "30m": "month",
    "1h": "month",
    "1d": "year",
    "1w": "year",
    "1mo": "year",
}
# Short names used across the app, by instrument key. Config can add more (universe.yaml).
DEFAULT_ALIASES = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT",
    "NIFTYNXT50": "NSE_INDEX|Nifty Next 50",
    "INDIAVIX": "NSE_INDEX|India VIX",
}
# Daily ATM implied volatility per underlying, kept as a derived series.
ATM_IV = "atm_iv"
OLD_IV_PREFIX = "iv_"
OLD_INDEX_PREFIX = "index_"
# Segment for names that are not instrument keys, such as test data or ad hoc imports.
CUSTOM_SEGMENT = "CUSTOM"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def folder_name(name: str) -> str:
    """A name that is safe as a folder: "Nifty 50" becomes "Nifty_50"."""
    safe = _UNSAFE.sub("_", name).strip("_")
    if not safe:
        raise ValueError(f"Cannot store data under the name {name!r}")
    return safe


def split_key(instrument_key: str) -> tuple[str, str]:
    """("NSE_INDEX", "Nifty 50") from "NSE_INDEX|Nifty 50"."""
    segment, _, name = instrument_key.partition("|")
    if not name:
        return CUSTOM_SEGMENT, instrument_key
    return segment, name


def _periods(ts: pd.Series, granularity: str) -> pd.Series:
    times = pd.to_datetime(ts)
    if getattr(times.dt, "tz", None) is not None:
        times = times.dt.tz_convert(IST)
    return times.dt.strftime("%Y" if granularity == "year" else "%Y-%m")


def _period_of(day: date, granularity: str) -> str:
    return f"{day:%Y}" if granularity == "year" else f"{day:%Y-%m}"


def _write_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def _upsert(folder: Path, frame: pd.DataFrame, granularity: str) -> list[Path]:
    if frame.empty:
        return []
    written = []
    for period, part in frame.groupby(_periods(frame["ts"], granularity), sort=True):
        path = folder / f"{granularity}={period}" / FILE
        merged = (
            pd.concat([pd.read_parquet(path), part], ignore_index=True) if path.exists() else part
        )
        merged = (
            merged.drop_duplicates(subset="ts", keep="last")
            .sort_values("ts")
            .reset_index(drop=True)
        )
        _write_atomic(merged, path)
        written.append(path)
    return written


def _read(
    folder: Path, granularity: str, start: date | None = None, end: date | None = None
) -> pd.DataFrame:
    low = _period_of(start, granularity) if start else None
    high = _period_of(end, granularity) if end else None
    frames = []
    for path in sorted(folder.glob(f"{granularity}=*/{FILE}")):
        period = path.parent.name.split("=", 1)[1]
        if (low and period < low) or (high and period > high):
            continue
        frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True).sort_values("ts").reset_index(drop=True)
    if start or end:
        days = pd.to_datetime(frame["ts"])
        if getattr(days.dt, "tz", None) is not None:
            days = days.dt.tz_convert(IST)
        days = days.dt.date
        keep = pd.Series(True, index=frame.index)
        if start:
            keep &= days >= start
        if end:
            keep &= days <= end
        frame = frame[keep].reset_index(drop=True)
    return frame


class CandleStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def folder(self, instrument_key: str, interval: str = DAILY) -> Path:
        if interval not in INTERVALS:
            raise ValueError(f"Unknown candle interval {interval!r}")
        segment, name = split_key(instrument_key)
        return (
            self.data_dir
            / CANDLES
            / f"interval={interval}"
            / f"segment={folder_name(segment)}"
            / f"symbol={folder_name(name)}"
        )

    def write(self, instrument_key: str, frame: pd.DataFrame, interval: str = DAILY) -> list[Path]:
        return _upsert(self.folder(instrument_key, interval), frame, INTERVALS[interval])

    def read(
        self,
        instrument_key: str,
        interval: str = DAILY,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        return _read(self.folder(instrument_key, interval), INTERVALS[interval], start, end)

    def series_folder(self, name: str, symbol: str) -> Path:
        return (
            self.data_dir / SERIES / f"name={folder_name(name)}" / f"symbol={folder_name(symbol)}"
        )

    def write_series(self, name: str, symbol: str, frame: pd.DataFrame) -> list[Path]:
        return _upsert(self.series_folder(name, symbol), frame, "year")

    def read_series(self, name: str, symbol: str) -> pd.DataFrame:
        return _read(self.series_folder(name, symbol), "year")


@dataclass(frozen=True)
class Target:
    """Where an old flat file belongs: candles of an instrument, or a derived series."""

    instrument_key: str | None = None
    series: str | None = None
    symbol: str | None = None


def resolve_name(name: str, aliases: dict[str, str]) -> Target:
    """Where data stored under a short name lives.

    "NSE_INDEX|Nifty 50" and "NIFTY" are the same instrument. "iv_NIFTY" is the ATM IV series.
    "index_Nifty_50" is the old overview name for an index. Anything else is kept as CUSTOM.
    """
    if "|" in name:
        return Target(instrument_key=name)
    if name in aliases:
        return Target(instrument_key=aliases[name])
    if name.startswith(OLD_IV_PREFIX):
        return Target(series=ATM_IV, symbol=name[len(OLD_IV_PREFIX) :])
    if name.startswith(OLD_INDEX_PREFIX):
        index = name[len(OLD_INDEX_PREFIX) :].replace("_", " ")
        return Target(instrument_key=f"NSE_INDEX|{index}")
    return Target(instrument_key=f"{CUSTOM_SEGMENT}|{name}")


@dataclass
class MigrationResult:
    moved: int = 0
    unknown: list[str] | None = None


def migrate_flat_layout(data_dir: Path, resolve: Callable[[str], Target | None]) -> MigrationResult:
    """Move files from the old layout (candles/<name>.parquet and
    intraday/date=<day>/<name>.parquet) into the organised one. Safe to run from several
    processes at once and to run again. Files whose name cannot be resolved stay where they are.
    """
    data_dir = Path(data_dir)
    store = CandleStore(data_dir)
    result = MigrationResult(unknown=[])
    old_daily = sorted((data_dir / CANDLES).glob("*.parquet"))
    old_minutes = sorted((data_dir / "intraday").glob("date=*/*.parquet"))
    if not old_daily and not old_minutes:
        return result
    lock_path = data_dir / CANDLES / ".migrate.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for path, interval in [(p, DAILY) for p in old_daily] + [(p, MINUTE) for p in old_minutes]:
            if not path.exists():
                continue  # Another process moved it while this one waited.
            target = resolve(path.stem)
            if target is None:
                result.unknown.append(str(path.relative_to(data_dir)))
                continue
            frame = pd.read_parquet(path)
            # The old data goes in first, so anything newer already in the new layout wins.
            if target.series:
                current = store.read_series(target.series, target.symbol)
                store.write_series(target.series, target.symbol, frame)
                if not current.empty:
                    store.write_series(target.series, target.symbol, current)
            else:
                current = store.read(target.instrument_key, interval)
                store.write(target.instrument_key, frame, interval)
                if not current.empty:
                    store.write(target.instrument_key, current, interval)
            path.unlink()
            result.moved += 1
        for folder in (data_dir / "intraday").glob("date=*"):
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        intraday = data_dir / "intraday"
        if intraday.is_dir() and not any(intraday.iterdir()):
            intraday.rmdir()
    if result.unknown:
        log.warning("Left in the old layout, name not recognised: %s", ", ".join(result.unknown))
    return result

"""Option chain snapshots and contract lists on disk.

    options/chains/segment=<NSE_FO|BSE_FO|MCX_FO>/underlying=<NIFTY>/date=<YYYY-MM-DD>/
        snapshot-<HHMMSS>-<expiry>.parquet   written every few minutes during the session
        part.parquet                         the whole day in one file, once it is compacted
    options/contracts/date=<YYYY-MM-DD>/part.parquet
                                             the listed option contracts on that day

Options are filed by underlying, expiry, strike and type rather than by instrument key,
because the exchange reuses an option's numeric key once the contract has expired. Every row
carries `ts` (snapshot time), `expiry`, `strike` and `option_type`, so a day reads as one table.

After the close each day's snapshots are compacted into one sorted file: about 2,000 small
files a day become 3, which keeps reads fast and the S3 copy tidy.
"""

from __future__ import annotations

import fcntl
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
OPTIONS = "options"
CHAINS = "chains"
CONTRACTS = "contracts"
DAY_FILE = "part.parquet"
SNAPSHOT_PREFIX = "snapshot-"
DEFAULT_SEGMENT = "NSE_FO"
# Snapshots of a day are compacted once the session is over.
COMPACT_AFTER = time(15, 45)


def chains_root(data_dir: Path) -> Path:
    return Path(data_dir) / OPTIONS / CHAINS


def contracts_root(data_dir: Path) -> Path:
    return Path(data_dir) / OPTIONS / CONTRACTS


def _write_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def segment_of(chain: pd.DataFrame) -> str:
    """The exchange segment from the contracts' instrument keys, such as NSE_FO."""
    if "instrument_key" in chain and not chain.empty:
        segment = str(chain["instrument_key"].iloc[0]).partition("|")[0]
        if segment:
            return segment
    return DEFAULT_SEGMENT


def day_folder(data_dir: Path, segment: str, underlying: str, day: date) -> Path:
    return (
        chains_root(data_dir)
        / f"segment={segment}"
        / f"underlying={underlying}"
        / f"date={day.isoformat()}"
    )


def write_snapshot(
    data_dir: Path, underlying: str, ts: datetime, chain: pd.DataFrame, expiry: str
) -> Path:
    folder = day_folder(data_dir, segment_of(chain), underlying, ts.date())
    path = folder / f"{SNAPSHOT_PREFIX}{ts.strftime('%H%M%S')}-{expiry}.parquet"
    _write_atomic(chain, path)
    return path


def read_chains(data_dir: Path, underlying: str, start: date, end: date) -> pd.DataFrame:
    root = chains_root(data_dir)
    if not next(root.glob("*/*/*/*.parquet"), None):
        return pd.DataFrame()
    query = """
        SELECT * EXCLUDE (segment)
        FROM read_parquet(?, hive_partitioning = true, union_by_name = true)
        WHERE underlying = ? AND date BETWEEN ? AND ?
        ORDER BY ts, expiry, strike, option_type
    """
    with duckdb.connect() as connection:
        return connection.execute(
            query, [str(root / "*" / "*" / "*" / "*.parquet"), underlying, start, end]
        ).df()


def snapshot_files(folder: Path) -> list[Path]:
    return sorted(folder.glob(f"{SNAPSHOT_PREFIX}*.parquet"))


def _folder_day(folder: Path) -> date:
    return date.fromisoformat(folder.name.split("=", 1)[1])


def compact_day(folder: Path) -> int:
    """Fold the day's snapshots into part.parquet. Returns how many snapshots it merged.

    Only the files read are removed afterwards, so a snapshot written meanwhile waits for the
    next run. The day file is replaced in one step, so a crash leaves the old state intact.
    """
    snapshots = snapshot_files(folder)
    if not snapshots:
        return 0
    frames = [pd.read_parquet(path) for path in snapshots]
    if (folder / DAY_FILE).exists():
        frames.insert(0, pd.read_parquet(folder / DAY_FILE))
    # pandas keeps the IST time zone on `ts` exactly as collected.
    day = (
        pd.concat(frames, ignore_index=True)
        .sort_values(["ts", "expiry", "strike", "option_type"], kind="stable")
        .reset_index(drop=True)
    )
    tmp = folder / f"{DAY_FILE}.tmp"
    day.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, folder / DAY_FILE)
    for path in snapshots:
        path.unlink()
    return len(snapshots)


def day_closed(day: date, now: datetime) -> bool:
    local = now.astimezone(IST)
    return day < local.date() or (day == local.date() and local.time() >= COMPACT_AFTER)


@dataclass
class CompactResult:
    days: list[str] = field(default_factory=list)
    snapshots: int = 0


def compact_chains(data_dir: Path, now: datetime) -> CompactResult:
    """Compact every finished day that still has loose snapshots."""
    result = CompactResult()
    root = chains_root(data_dir)
    if not root.exists():
        return result
    with _lock(root):
        for folder in sorted(root.glob("segment=*/underlying=*/date=*")):
            if not day_closed(_folder_day(folder), now) or not snapshot_files(folder):
                continue
            result.snapshots += compact_day(folder)
            result.days.append(folder.relative_to(root).as_posix())
    return result


class _lock:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / ".lock"

    def __enter__(self):
        self.file = self.path.open("w")
        fcntl.flock(self.file, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        self.file.close()


def write_contracts(data_dir: Path, day: date, contracts: pd.DataFrame) -> Path:
    path = contracts_root(data_dir) / f"date={day.isoformat()}" / DAY_FILE
    _write_atomic(contracts, path)
    return path


def contracts_path(data_dir: Path, day: date) -> Path:
    return contracts_root(data_dir) / f"date={day.isoformat()}" / DAY_FILE


def read_contracts(data_dir: Path, as_of: date) -> pd.DataFrame | None:
    """The newest contract list dated on or before `as_of`, so old lot sizes stay correct."""
    days = sorted(
        _folder_day(folder)
        for folder in contracts_root(data_dir).glob("date=*")
        if (folder / DAY_FILE).exists()
    )
    eligible = [day for day in days if day <= as_of]
    return pd.read_parquet(contracts_path(data_dir, eligible[-1])) if eligible else None


def migrate_old_layout(data_dir: Path) -> int:
    """Move chains/underlying=X/date=D/HHMMSS_<expiry>.parquet and contracts/<day>.parquet
    into the options folder. Safe to run again, and from several processes."""
    data_dir = Path(data_dir)
    old_chains = sorted((data_dir / "chains").glob("underlying=*/date=*/*.parquet"))
    old_contracts = sorted((data_dir / "contracts").glob("*.parquet"))
    if not old_chains and not old_contracts:
        return 0
    moved = 0
    with _lock(chains_root(data_dir)):
        for path in old_chains:
            if not path.exists():
                continue
            underlying = path.parent.parent.name.split("=", 1)[1]
            day = _folder_day(path.parent)
            stamp, _, expiry = path.stem.partition("_")
            segment = segment_of(pd.read_parquet(path, columns=["instrument_key"]))
            target = day_folder(data_dir, segment, underlying, day)
            target.mkdir(parents=True, exist_ok=True)
            os.replace(path, target / f"{SNAPSHOT_PREFIX}{stamp}-{expiry}.parquet")
            moved += 1
        for path in old_contracts:
            if not path.exists():
                continue
            target = contracts_path(data_dir, date.fromisoformat(path.stem))
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, target)
            moved += 1
        for old in ("chains", "contracts"):
            _remove_empty(data_dir / old)
    return moved


def _remove_empty(folder: Path) -> None:
    if not folder.is_dir():
        return
    for child in sorted(folder.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if child.is_dir() and not any(child.iterdir()):
            child.rmdir()
    if not any(folder.iterdir()):
        folder.rmdir()

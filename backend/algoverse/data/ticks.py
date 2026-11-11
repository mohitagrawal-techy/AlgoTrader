"""Stores every live feed tick as Parquet, one folder per trading day.

Ticks land in small part files so a crash loses only the last few seconds. After the close each
day is merged into one sorted file. Query them with `read_ticks` or DuckDB.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
TIMESTAMP = pa.timestamp("ms", tz="Asia/Kolkata")
MANIFEST = "merging.json"
DAY_FILE = "day.parquet"
MERGE_MEMORY = "2GB"
_DEPTH = [
    pa.field(name, kind)
    for level in range(1, 6)
    for name, kind in (
        (f"bid_qty_{level}", pa.int64()),
        (f"bid_{level}", pa.float64()),
        (f"ask_qty_{level}", pa.int64()),
        (f"ask_{level}", pa.float64()),
    )
]
SCHEMA = pa.schema(
    [
        pa.field("received_at", TIMESTAMP),
        pa.field("feed_ts", pa.int64()),
        pa.field("instrument_key", pa.string()),
        pa.field("symbol", pa.string()),
        pa.field("instrument", pa.string()),
        pa.field("expiry", pa.date32()),
        pa.field("strike", pa.float64()),
        pa.field("option_type", pa.string()),
        pa.field("message_type", pa.string()),
        pa.field("request_mode", pa.string()),
        pa.field("ltp", pa.float64()),
        pa.field("ltt", pa.int64()),
        pa.field("ltq", pa.int64()),
        pa.field("cp", pa.float64()),
        pa.field("atp", pa.float64()),
        pa.field("vtt", pa.int64()),
        pa.field("oi", pa.float64()),
        pa.field("iv", pa.float64()),
        pa.field("tbq", pa.float64()),
        pa.field("tsq", pa.float64()),
        pa.field("iep", pa.float64()),
        pa.field("rp", pa.float64()),
        pa.field("ieq", pa.int64()),
        pa.field("iiq_total", pa.int64()),
        pa.field("iiq_m", pa.int64()),
        pa.field("cas_eligible", pa.bool_()),
        pa.field("delta", pa.float64()),
        pa.field("theta", pa.float64()),
        pa.field("gamma", pa.float64()),
        pa.field("vega", pa.float64()),
        pa.field("rho", pa.float64()),
        pa.field("day_open", pa.float64()),
        pa.field("day_high", pa.float64()),
        pa.field("day_low", pa.float64()),
        pa.field("day_close", pa.float64()),
        *_DEPTH,
    ]
)


def ticks_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "ticks"


def _day_dir(data_dir: Path, day: date) -> Path:
    return ticks_dir(data_dir) / f"date={day.isoformat()}"


def write_table(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), tmp, compression="zstd")
    os.replace(tmp, path)


class TickWriter:
    """Buffers ticks and writes them to the folder of the day they were received."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._buffer: list[dict] = []
        self._sequence = 0
        self.rows_written = 0

    def __len__(self) -> int:
        return len(self._buffer)

    def add(self, rows: list[dict]) -> None:
        self._buffer.extend(rows)

    def flush(self) -> None:
        if not self._buffer:
            return
        by_day: dict[date, list[dict]] = defaultdict(list)
        for row in self._buffer:
            by_day[row["received_at"].date()].append(row)
        for day, rows in by_day.items():
            self._sequence += 1
            stamp = rows[-1]["received_at"].strftime("%H%M%S%f")
            name = f"part-{stamp}-{self._sequence}.parquet"
            write_table(_day_dir(self.data_dir, day) / name, rows)
            self.rows_written += len(rows)
        self._buffer = []

    def append_status(self, received_at: datetime, status: dict[str, str]) -> None:
        path = _day_dir(self.data_dir, received_at.date()) / "market_status.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as file:
            entry = {"received_at": received_at.isoformat(), "status": status}
            file.write(json.dumps(entry) + "\n")


def _finish_merge(folder: Path) -> None:
    """Completes a merge that a crash interrupted, so no tick is lost or counted twice."""
    manifest = folder / MANIFEST
    if not manifest.exists():
        return
    tmp = folder / f"{DAY_FILE}.tmp"
    if tmp.exists():
        os.replace(tmp, folder / DAY_FILE)
    for name in json.loads(manifest.read_text()):
        (folder / name).unlink(missing_ok=True)
    manifest.unlink()


def merge_day(folder: Path) -> int:
    """Merge a day's part files into one sorted file. Returns the number of parts merged."""
    _finish_merge(folder)
    (folder / f"{DAY_FILE}.tmp").unlink(missing_ok=True)
    parts = sorted(folder.glob("part-*.parquet"))
    if not parts:
        return 0
    sources = [str(path) for path in parts]
    if (folder / DAY_FILE).exists():
        sources.append(str(folder / DAY_FILE))
    tmp = folder / f"{DAY_FILE}.tmp"
    with duckdb.connect() as connection:
        connection.execute(f"SET memory_limit = '{MERGE_MEMORY}'")
        connection.execute("SET temp_directory = ?", [str(folder / ".merge")])
        connection.execute("SET preserve_insertion_order = false")
        connection.execute(
            f"COPY (SELECT * FROM read_parquet(?) ORDER BY instrument_key, received_at) "
            f"TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)",
            [sources],
        )
    shutil.rmtree(folder / ".merge", ignore_errors=True)
    manifest = folder / MANIFEST
    manifest_tmp = folder / f"{MANIFEST}.tmp"
    manifest_tmp.write_text(json.dumps([path.name for path in parts]))
    os.replace(manifest_tmp, manifest)
    _finish_merge(folder)
    return len(parts)


def merge_pending(data_dir: Path) -> int:
    merged = 0
    for folder in sorted(ticks_dir(data_dir).glob("date=*")):
        if list(folder.glob("part-*.parquet")) or (folder / MANIFEST).exists():
            merged += merge_day(folder)
    return merged


def _files_between(data_dir: Path, start: date, end: date) -> list[str]:
    files = []
    for folder in sorted(ticks_dir(data_dir).glob("date=*")):
        if start <= date.fromisoformat(folder.name[5:]) <= end:
            files += [str(path) for path in folder.glob("*.parquet")]
    return files


def read_ticks(
    data_dir: Path, start: date, end: date, symbol: str | None = None
) -> pd.DataFrame:
    files = _files_between(data_dir, start, end)
    if not files:
        return pd.DataFrame(columns=SCHEMA.names)
    where = "WHERE symbol = ?" if symbol else ""
    parameters = [files, symbol] if symbol else [files]
    query = f"SELECT * FROM read_parquet(?) {where} ORDER BY received_at"
    with duckdb.connect() as connection:
        return connection.execute(query, parameters).df()


def stored_days(data_dir: Path) -> list[dict]:
    days = []
    for folder in sorted(ticks_dir(data_dir).glob("date=*")):
        files = list(folder.glob("*.parquet"))
        if files:
            days.append(
                {
                    "date": folder.name[5:],
                    "rows": sum(pq.read_metadata(path).num_rows for path in files),
                    "bytes": sum(path.stat().st_size for path in files),
                    "files": len(files),
                }
            )
    return days

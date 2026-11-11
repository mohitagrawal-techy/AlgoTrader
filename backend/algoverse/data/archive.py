"""Copies the data folder to S3 once each day is finished, and trims old ticks locally.

Rules that keep this safe:
- Writes only under the configured prefix, which may not overlap the history source folder.
- Never deletes anything in S3.
- A day's files are uploaded only once that day is finished, so S3 never holds a half day.
- Local ticks are deleted only after S3 reports the same size for every file of that day.
- The Upstox login, the live price cache and the dashboard cache are never uploaded.
"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
ARCHIVE_DIR = "archive"
MANIFEST = "manifest.sqlite"
DAY_PART = re.compile(r"^date=(\d{4}-\d{2}-\d{2})$")
# Secrets, files rewritten every few seconds, and caches rebuilt from other data.
SKIP_TOP = {ARCHIVE_DIR, "live", "dashboard", "upstox_token.json"}
SKIP_SUFFIXES = (".tmp", ".sqlite-journal", ".sqlite-wal", ".sqlite-shm")
DATABASE_SUFFIXES = (".sqlite", ".db")
# Ticks still being written (part files) or merged (manifest) mean the day is not finished.
UNFINISHED_TICKS = ("part-*.parquet", "merging.json")


class ArchiveConfig(BaseModel):
    enabled: bool = True
    bucket: str
    prefix: str
    region: str = "us-east-1"
    # Days count as finished at this time (IST), after the feed has merged the day's ticks.
    finished_after: time = time(16, 30)
    keep_tick_days: int | None = Field(default=30, ge=1)
    workers: int = Field(default=8, ge=1, le=32)
    # Folders in the bucket this job must never write into.
    protected_prefixes: list[str] = Field(default_factory=lambda: ["ohlc_new/"])

    @field_validator("prefix")
    @classmethod
    def _folder(cls, value: str) -> str:
        value = value.strip("/")
        if not value:
            raise ValueError("Set a folder for uploads, so the rest of the bucket is untouched.")
        return value + "/"

    @model_validator(mode="after")
    def _not_protected(self) -> ArchiveConfig:
        for protected in self.protected_prefixes:
            if self.prefix.startswith(protected) or protected.startswith(self.prefix):
                raise ValueError(f"The upload folder overlaps {protected}, which is read-only.")
        return self


def load_archive_config(config_dir: Path) -> ArchiveConfig:
    with (Path(config_dir) / "archive_s3.yaml").open() as file:
        return ArchiveConfig.model_validate(yaml.safe_load(file))


@dataclass
class ArchiveResult:
    uploaded: int = 0
    uploaded_bytes: int = 0
    unchanged: int = 0
    waiting_days: set[str] = field(default_factory=set)
    databases: int = 0
    pruned_days: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)


class Manifest:
    """What has been uploaded, by path, size and modification time."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, size INTEGER, "
            "mtime_ns INTEGER, key TEXT, uploaded_at TEXT)"
        )

    def get(self, path: str) -> tuple[int, int] | None:
        row = self._db.execute(
            "SELECT size, mtime_ns FROM files WHERE path = ?", (path,)
        ).fetchone()
        return (row[0], row[1]) if row else None

    def record(self, path: str, size: int, mtime_ns: int, key: str, at: datetime) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?)",
            (path, size, mtime_ns, key, at.isoformat()),
        )

    def commit(self) -> None:
        self._db.commit()

    def close(self) -> None:
        self._db.commit()
        self._db.close()


def _day_of(relative: Path) -> date | None:
    for part in relative.parts[:-1]:
        match = DAY_PART.match(part)
        if match:
            return date.fromisoformat(match.group(1))
    return None


def _ticks_unfinished(data_dir: Path, day: date) -> bool:
    folder = data_dir / "ticks" / f"date={day.isoformat()}"
    return any(next(folder.glob(pattern), None) for pattern in UNFINISHED_TICKS)


def _chains_unfinished(data_dir: Path, day: date) -> bool:
    """Loose snapshots mean the day has not been compacted yet."""
    root = data_dir / "options" / "chains"
    pattern = f"segment=*/underlying=*/date={day.isoformat()}/snapshot-*"
    return next(root.glob(pattern), None) is not None


def day_finished(data_dir: Path, day: date, now: datetime, config: ArchiveConfig) -> bool:
    today = now.date()
    if day > today or (day == today and now.time() < config.finished_after):
        return False
    return not _ticks_unfinished(data_dir, day) and not _chains_unfinished(data_dir, day)


def _wanted_files(data_dir: Path):
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(data_dir)
        if relative.parts[0] in SKIP_TOP or path.name.endswith(SKIP_SUFFIXES):
            continue
        if path.name.startswith("."):
            continue  # Lock files and other working files.
        if path.name.endswith(DATABASE_SUFFIXES):
            continue  # Copied safely by _backup_databases instead.
        yield path, relative


def _put(client, config: ArchiveConfig, path: Path, key: str, size: int) -> None:
    client.upload_file(str(path), config.bucket, key, ExtraArgs={"ServerSideEncryption": "AES256"})
    stored = client.head_object(Bucket=config.bucket, Key=key)["ContentLength"]
    if stored != size:
        raise OSError(f"S3 holds {stored} bytes, expected {size}")


def _key(config: ArchiveConfig, relative: Path | str) -> str:
    key = config.prefix + str(relative).replace("\\", "/")
    assert key.startswith(config.prefix)
    return key


def _backup_databases(
    client, config: ArchiveConfig, data_dir: Path, now: datetime, result: ArchiveResult
) -> None:
    """A consistent copy of each SQLite database, one per day, even while it is in use."""
    staging = data_dir / ARCHIVE_DIR / "db"
    staging.mkdir(parents=True, exist_ok=True)
    for path in sorted(data_dir.rglob("*")):
        relative = path.relative_to(data_dir)
        if not path.is_file() or not path.name.endswith(DATABASE_SUFFIXES):
            continue
        if relative.parts[0] in SKIP_TOP:
            continue
        copy = staging / relative.as_posix().replace("/", "__")
        try:
            with sqlite3.connect(path) as source, sqlite3.connect(copy) as target:
                source.backup(target)
            key = _key(config, f"db/date={now.date().isoformat()}/{relative.as_posix()}")
            _put(client, config, copy, key, copy.stat().st_size)
            result.databases += 1
        except (sqlite3.Error, OSError) as exc:
            result.failures[str(relative)] = str(exc)
        finally:
            copy.unlink(missing_ok=True)


def _prune_ticks(
    client,
    config: ArchiveConfig,
    data_dir: Path,
    manifest: Manifest,
    now: datetime,
    result: ArchiveResult,
) -> None:
    if config.keep_tick_days is None:
        return
    cutoff = now.date() - timedelta(days=config.keep_tick_days)
    for folder in sorted((data_dir / "ticks").glob("date=*")):
        match = DAY_PART.match(folder.name)
        if not match or date.fromisoformat(match.group(1)) >= cutoff:
            continue
        files = [p for p in folder.rglob("*") if p.is_file()]
        if not files or not all(
            _safely_stored(client, config, data_dir, manifest, p) for p in files
        ):
            log.warning("Keeping %s: not every file is confirmed in S3", folder.name)
            continue
        shutil.rmtree(folder)
        result.pruned_days.append(match.group(1))


def _safely_stored(client, config, data_dir: Path, manifest: Manifest, path: Path) -> bool:
    relative = path.relative_to(data_dir)
    stat = path.stat()
    if manifest.get(str(relative)) != (stat.st_size, stat.st_mtime_ns):
        return False
    try:
        head = client.head_object(Bucket=config.bucket, Key=_key(config, relative))
    except Exception:  # Any doubt about the S3 copy keeps the local one.
        return False
    return head["ContentLength"] == stat.st_size


def archive(
    client,
    config: ArchiveConfig,
    data_dir: Path,
    now: datetime,
    dry_run: bool = False,
) -> ArchiveResult:
    data_dir = Path(data_dir)
    result = ArchiveResult()
    manifest = Manifest(data_dir / ARCHIVE_DIR / MANIFEST)
    finished: dict[date, bool] = {}
    try:
        pending = []
        for path, relative in _wanted_files(data_dir):
            day = _day_of(relative)
            if day is not None:
                if day not in finished:
                    finished[day] = day_finished(data_dir, day, now, config)
                if not finished[day]:
                    result.waiting_days.add(day.isoformat())
                    continue
            stat = path.stat()
            if manifest.get(str(relative)) == (stat.st_size, stat.st_mtime_ns):
                result.unchanged += 1
                continue
            pending.append((path, relative, stat))
        if dry_run:
            result.uploaded = len(pending)
            result.uploaded_bytes = sum(stat.st_size for _, _, stat in pending)
            return result

        def upload(item):
            path, relative, stat = item
            _put(client, config, path, _key(config, relative), stat.st_size)
            return item

        with ThreadPoolExecutor(max_workers=config.workers) as pool:
            futures = {pool.submit(upload, item): item for item in pending}
            for future, (_, relative, stat) in futures.items():
                try:
                    future.result()
                except Exception as exc:  # One failed file must not stop the rest.
                    result.failures[str(relative)] = str(exc)
                    continue
                manifest.record(
                    str(relative), stat.st_size, stat.st_mtime_ns, _key(config, relative), now
                )
                result.uploaded += 1
                result.uploaded_bytes += stat.st_size
                if result.uploaded % 200 == 0:
                    manifest.commit()
        manifest.commit()
        _backup_databases(client, config, data_dir, now, result)
        _prune_ticks(client, config, data_dir, manifest, now, result)
    finally:
        manifest.close()
    return result


def make_client(config: ArchiveConfig):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        region_name=config.region,
        config=Config(retries={"max_attempts": 5, "mode": "standard"}),
    )

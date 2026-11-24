"""Pulls historical F&O data from an S3 bucket into the backtest store. Read-only.

Accepted, in .csv, .csv.gz, .zip or .parquet: NSE bhavcopy files, or daily contract rows that
already use the store's columns. Objects that have not changed since the last pull are skipped,
so a pull can be stopped and run again at any time.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import os
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import boto3
import pandas as pd
import pyarrow.parquet as pq
import yaml
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from pydantic import BaseModel

from algoverse.backtest.bhavcopy import (
    COLUMNS,
    BhavcopyError,
    history_dir,
    parse_bhavcopy,
    store_day,
)

log = logging.getLogger(__name__)
SUFFIXES = (".csv", ".csv.gz", ".zip", ".parquet")
MAX_OBJECT_BYTES = 512 * 1024 * 1024
FOOTER_BYTES = 1024 * 1024
CANDLE_COLUMNS = {"instrument_key", "candle_time", "open", "high", "low", "close"}
ACCESS_CODES = {
    "AccessDenied": "AWS refused access. The credentials need s3:ListBucket and s3:GetObject.",
    "NoSuchBucket": "That bucket does not exist. Check bucket in config/history_s3.yaml.",
    "InvalidAccessKeyId": "AWS does not recognise this access key.",
    "SignatureDoesNotMatch": "The AWS secret key is wrong.",
    "ExpiredToken": "The AWS session token has expired.",
    "PermanentRedirect": "The bucket is in another region. Check region in config/history_s3.yaml.",
    "AuthorizationHeaderMalformed": "The region looks wrong. Check config/history_s3.yaml.",
}


class S3Config(BaseModel):
    bucket: str = ""
    prefix: str = ""
    region: str = "ap-south-1"


class S3AccessError(Exception):
    """The bucket could not be reached. The message says what to fix."""


@dataclass
class S3PullResult:
    listed: int = 0
    unchanged: int = 0
    ignored: int = 0
    pending: int = 0
    sessions: list[date] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def load_s3_config(config_dir: Path) -> S3Config:
    with (Path(config_dir) / "history_s3.yaml").open() as file:
        return S3Config.model_validate(yaml.safe_load(file) or {})


def make_client(config: S3Config):
    return boto3.client(
        "s3",
        region_name=config.region,
        config=Config(retries={"max_attempts": 5, "mode": "standard"}),
    )


def _access_message(error: Exception) -> str:
    if isinstance(error, NoCredentialsError):
        return "No AWS credentials found. Add them to .env, then restart."
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code", "")
        return ACCESS_CODES.get(code, f"AWS returned {code or 'an error'}.")
    return "Could not reach AWS."


def _list_objects(client, config: S3Config) -> Iterator[dict]:
    params = {"Bucket": config.bucket, "Prefix": config.prefix}
    while True:
        try:
            page = client.list_objects_v2(**params)
        except (ClientError, BotoCoreError) as exc:
            raise S3AccessError(_access_message(exc)) from exc
        yield from page.get("Contents", [])
        if not page.get("IsTruncated"):
            return
        params["ContinuationToken"] = page["NextContinuationToken"]


def _unpack(key: str, content: bytes) -> tuple[bytes, bool]:
    """The file's bytes, and whether they are Parquet."""
    name = key.lower()
    if name.endswith(".parquet"):
        return content, True
    if name.endswith(".csv"):
        return content, False
    if name.endswith(".gz"):
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as stream:
            data = stream.read(MAX_OBJECT_BYTES + 1)
        if len(data) > MAX_OBJECT_BYTES:
            raise BhavcopyError("file is too large once unpacked")
        return data, False
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = [info for info in archive.infolist() if info.filename.lower().endswith(".csv")]
        if len(names) != 1:
            raise BhavcopyError("zip file must contain exactly one CSV")
        if names[0].file_size > MAX_OBJECT_BYTES:
            raise BhavcopyError("file is too large once unpacked")
        return archive.read(names[0]), False


def _normalise_rows(raw: pd.DataFrame, symbols: set[str]) -> pd.DataFrame:
    """Rows already in the store's layout, for the configured symbols."""
    frame = raw[raw["symbol"].isin(symbols) & raw["instrument"].isin(["OPT", "FUT"])].copy()
    if frame.empty:
        raise BhavcopyError("file has no rows for the configured symbols")
    for column in ("date", "expiry"):
        frame[column] = pd.to_datetime(frame[column]).dt.date
    numeric = (
        "strike",
        "open",
        "high",
        "low",
        "close",
        "settle",
        "volume",
        "oi",
        "oi_change",
        "lot_size",
        "underlying_price",
    )
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "expiry", "close", "lot_size"])
    frame["lot_size"] = frame["lot_size"].astype(int)
    frame["option_type"] = frame["option_type"].astype(object).where(frame["instrument"] == "OPT")
    for column in ("open", "high", "low", "volume", "oi", "oi_change"):
        frame[column] = frame[column].fillna(0.0)
    if frame.empty:
        raise BhavcopyError("no usable rows after removing incomplete ones")
    return frame[COLUMNS].reset_index(drop=True)


def parse_object(
    key: str,
    content: bytes,
    symbols: list[str],
    lots: dict[str, list[tuple[date, int]]],
) -> list[pd.DataFrame]:
    """One frame per trading date. Raises BhavcopyError when the layout is not recognised."""
    try:
        data, is_parquet = _unpack(key, content)
    except (zipfile.BadZipFile, OSError, EOFError) as exc:
        raise BhavcopyError("the compressed file is damaged") from exc
    try:
        raw = (
            pd.read_parquet(io.BytesIO(data))
            if is_parquet
            else pd.read_csv(io.BytesIO(data), dtype=str, skipinitialspace=True, nrows=0)
        )
    except (ValueError, OSError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise BhavcopyError("not a readable data file") from exc
    columns = {str(column).strip() for column in raw.columns}
    if set(COLUMNS) <= columns:
        if not is_parquet:
            raw = pd.read_csv(io.BytesIO(data), dtype=str, skipinitialspace=True)
            raw.columns = [str(column).strip() for column in raw.columns]
        frame = _normalise_rows(raw, set(symbols))
        return [group.reset_index(drop=True) for _, group in frame.groupby("date")]
    if is_parquet:
        raise BhavcopyError(f"missing columns: {sorted(set(COLUMNS) - columns)}")
    return [parse_bhavcopy(data, symbols, lots)]


def _footer_problem(client, bucket: str, key: str) -> str | None:
    """Reads only a Parquet file's footer, so a file in the wrong layout is never downloaded."""
    tail = client.get_object(Bucket=bucket, Key=key, Range=f"bytes=-{FOOTER_BYTES}")["Body"].read()
    try:
        columns = set(pq.read_schema(io.BytesIO(tail)).names)
    except (ValueError, OSError):
        return None  # Too large a footer or a damaged file: the full read reports it properly.
    if set(COLUMNS) <= columns:
        return None
    if CANDLE_COLUMNS <= columns:
        return "holds per-instrument candles, not daily F&O contract rows"
    return f"missing columns: {sorted(set(COLUMNS) - columns)}"


def _manifest_path(data_dir: Path) -> Path:
    return Path(data_dir) / "history" / "s3_manifest.json"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(value))
    os.replace(tmp, path)


def _release_from_upstox(data_dir: Path, day: date) -> None:
    """Bucket data now holds this date, so the daily Upstox capture must not replace it."""
    path = Path(data_dir) / "history" / "upstox" / "written.json"
    written = _read_json(path, None)
    if written and day.isoformat() in written:
        _write_json(path, [item for item in written if item != day.isoformat()])


def pull_history(
    client,
    config: S3Config,
    data_dir: Path,
    symbols: list[str],
    lots: dict[str, list[tuple[date, int]]],
    dry_run: bool = False,
    limit: int | None = None,
    note: Callable[[str], None] = lambda _: None,
) -> S3PullResult:
    """Import new and changed objects. One bad file stops only itself."""
    if not config.bucket:
        raise S3AccessError("Set bucket in config/history_s3.yaml first.")
    result = S3PullResult()
    manifest: dict[str, str] = _read_json(_manifest_path(data_dir), {})
    objects = list(_list_objects(client, config))
    result.listed = len(objects)
    todo = []
    for item in objects:
        key = item["Key"]
        if not key.lower().endswith(SUFFIXES):
            result.ignored += 1
        elif manifest.get(key) == item.get("ETag"):
            result.unchanged += 1
        else:
            todo.append(item)
    result.pending = len(todo)
    if dry_run:
        return result
    days: set[date] = set()
    for number, item in enumerate(todo[:limit] if limit else todo, start=1):
        key = item["Key"]
        note(f"{number} of {len(todo)}: {key}")
        if item.get("Size", 0) > MAX_OBJECT_BYTES:
            result.failed[key] = "file is larger than the size limit"
            continue
        try:
            problem = (
                _footer_problem(client, config.bucket, key)
                if key.lower().endswith(".parquet")
                else None
            )
            if problem:
                result.failed[key] = problem
                continue
            content = client.get_object(Bucket=config.bucket, Key=key)["Body"].read()
        except (ClientError, BotoCoreError) as exc:
            result.failed[key] = _access_message(exc)
            continue
        try:
            frames = parse_object(key, content, symbols, lots)
        except BhavcopyError as exc:
            result.failed[key] = str(exc)
            continue
        for frame in frames:
            store_day(data_dir, frame)
            _release_from_upstox(data_dir, frame["date"].iloc[0])
            days.add(frame["date"].iloc[0])
        manifest[key] = item.get("ETag", "")
        _write_json(_manifest_path(data_dir), manifest)
    result.sessions = sorted(days)
    return result


def stored_sessions(data_dir: Path) -> int:
    return len(list(history_dir(data_dir).glob("*.parquet")))

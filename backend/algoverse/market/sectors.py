"""Stock-to-industry list, uploaded by the user from NSE's published constituents CSV."""

from __future__ import annotations

import csv
import io
import json
import os
import re
from pathlib import Path

REQUIRED = ("company name", "industry", "symbol", "isin code")
ISIN = re.compile(r"^IN[A-Z0-9]{10}$")
MAX_BYTES = 2_000_000


class SectorListError(ValueError):
    """The uploaded file could not be used. The message says why."""


def parse_sector_csv(content: bytes) -> list[dict]:
    if len(content) > MAX_BYTES:
        raise SectorListError("File is larger than the 2 MB limit.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SectorListError("File is not UTF-8 text.") from exc
    reader = csv.DictReader(io.StringIO(text))
    fields = {(name or "").strip().lower(): name for name in reader.fieldnames or []}
    missing = [column for column in REQUIRED if column not in fields]
    if missing:
        raise SectorListError(f"Missing columns: {', '.join(missing)}.")
    stocks: dict[str, dict] = {}
    for row in reader:
        isin = (row[fields["isin code"]] or "").strip().upper()
        symbol = (row[fields["symbol"]] or "").strip().upper()
        industry = (row[fields["industry"]] or "").strip()
        if ISIN.match(isin) and symbol and industry:
            stocks[isin] = {
                "isin": isin,
                "symbol": symbol,
                "name": (row[fields["company name"]] or "").strip(),
                "industry": industry,
            }
    if not stocks:
        raise SectorListError("No valid rows were found.")
    return list(stocks.values())


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / "market" / "sectors.json"


def save_sector_list(data_dir: Path, stocks: list[dict]) -> None:
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(stocks))
    os.replace(tmp, path)


def load_sector_list(data_dir: Path) -> list[dict]:
    try:
        return json.loads(_path(data_dir).read_text())
    except FileNotFoundError:
        return []

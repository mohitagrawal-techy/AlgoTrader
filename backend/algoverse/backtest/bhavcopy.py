"""Imports NSE F&O daily files that the user has downloaded by hand.

NSE's data terms prohibit automated collection without written consent, so nothing here
downloads. Headers are checked strictly: a file with unexpected columns is refused.
"""

from __future__ import annotations

import io
import os
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

COLUMNS = [
    "date",
    "symbol",
    "instrument",
    "expiry",
    "strike",
    "option_type",
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
]
UDIFF_REQUIRED = {
    "TradDt",
    "FinInstrmTp",
    "TckrSymb",
    "XpryDt",
    "StrkPric",
    "OptnTp",
    "OpnPric",
    "HghPric",
    "LwPric",
    "ClsPric",
    "SttlmPric",
    "OpnIntrst",
    "ChngInOpnIntrst",
    "TtlTradgVol",
    "NewBrdLotQty",
    "UndrlygPric",
}
LEGACY_REQUIRED = {
    "INSTRUMENT",
    "SYMBOL",
    "EXPIRY_DT",
    "STRIKE_PR",
    "OPTION_TYP",
    "OPEN",
    "HIGH",
    "LOW",
    "CLOSE",
    "SETTLE_PR",
    "CONTRACTS",
    "OPEN_INT",
    "CHG_IN_OI",
    "TIMESTAMP",
}
UDIFF_INDEX_TYPES = {"IDO": "OPT", "IDF": "FUT", "STO": "OPT", "STF": "FUT"}
LEGACY_TYPES = {"OPTIDX": "OPT", "FUTIDX": "FUT", "OPTSTK": "OPT", "FUTSTK": "FUT"}


class BhavcopyError(ValueError):
    """A file could not be understood. The message says why."""


def load_lot_sizes(config_dir: Path) -> dict[str, list[tuple[date, int]]]:
    with (config_dir / "lot_sizes.yaml").open() as f:
        raw = (yaml.safe_load(f) or {}).get("lot_sizes") or {}
    return {
        symbol: sorted((entry["from"], int(entry["lot_size"])) for entry in entries)
        for symbol, entries in raw.items()
    }


def _lot_on(table: dict[str, list[tuple[date, int]]], symbol: str, day: date) -> int | None:
    eligible = [lot for start, lot in table.get(symbol, []) if start <= day]
    return eligible[-1] if eligible else None


def _parse_udiff(raw: pd.DataFrame, symbols: set[str]) -> pd.DataFrame:
    rows = raw[raw["FinInstrmTp"].isin(UDIFF_INDEX_TYPES) & raw["TckrSymb"].isin(symbols)]
    return pd.DataFrame(
        {
            "date": pd.to_datetime(rows["TradDt"]).dt.date,
            "symbol": rows["TckrSymb"],
            "instrument": rows["FinInstrmTp"].map(UDIFF_INDEX_TYPES),
            "expiry": pd.to_datetime(rows["XpryDt"]).dt.date,
            "strike": pd.to_numeric(rows["StrkPric"], errors="coerce"),
            "option_type": rows["OptnTp"],
            "open": pd.to_numeric(rows["OpnPric"], errors="coerce"),
            "high": pd.to_numeric(rows["HghPric"], errors="coerce"),
            "low": pd.to_numeric(rows["LwPric"], errors="coerce"),
            "close": pd.to_numeric(rows["ClsPric"], errors="coerce"),
            "settle": pd.to_numeric(rows["SttlmPric"], errors="coerce"),
            "volume": pd.to_numeric(rows["TtlTradgVol"], errors="coerce"),
            "oi": pd.to_numeric(rows["OpnIntrst"], errors="coerce"),
            "oi_change": pd.to_numeric(rows["ChngInOpnIntrst"], errors="coerce"),
            "lot_size": pd.to_numeric(rows["NewBrdLotQty"], errors="coerce"),
            "underlying_price": pd.to_numeric(rows["UndrlygPric"], errors="coerce"),
        }
    )


def _parse_legacy(
    raw: pd.DataFrame, symbols: set[str], lots: dict[str, list[tuple[date, int]]]
) -> pd.DataFrame:
    rows = raw[raw["INSTRUMENT"].isin(LEGACY_TYPES) & raw["SYMBOL"].isin(symbols)]
    if rows.empty:
        return pd.DataFrame(columns=COLUMNS)
    day = pd.to_datetime(rows["TIMESTAMP"].iloc[0], format="%d-%b-%Y").date()
    lot_map = {symbol: _lot_on(lots, symbol, day) for symbol in rows["SYMBOL"].unique()}
    missing = sorted(symbol for symbol, lot in lot_map.items() if lot is None)
    if missing:
        raise BhavcopyError(f"{day}: add lot sizes to config/lot_sizes.yaml for {missing}")
    lot_size = rows["SYMBOL"].map(lot_map)
    return pd.DataFrame(
        {
            "date": day,
            "symbol": rows["SYMBOL"],
            "instrument": rows["INSTRUMENT"].map(LEGACY_TYPES),
            "expiry": pd.to_datetime(rows["EXPIRY_DT"], format="%d-%b-%Y").dt.date,
            "strike": pd.to_numeric(rows["STRIKE_PR"], errors="coerce"),
            "option_type": rows["OPTION_TYP"],
            "open": pd.to_numeric(rows["OPEN"], errors="coerce"),
            "high": pd.to_numeric(rows["HIGH"], errors="coerce"),
            "low": pd.to_numeric(rows["LOW"], errors="coerce"),
            "close": pd.to_numeric(rows["CLOSE"], errors="coerce"),
            "settle": pd.to_numeric(rows["SETTLE_PR"], errors="coerce"),
            "volume": pd.to_numeric(rows["CONTRACTS"], errors="coerce") * lot_size,
            "oi": pd.to_numeric(rows["OPEN_INT"], errors="coerce"),
            "oi_change": pd.to_numeric(rows["CHG_IN_OI"], errors="coerce"),
            "lot_size": lot_size,
            "underlying_price": float("nan"),
        }
    )


def parse_bhavcopy(
    content: bytes, symbols: Iterable[str], lots: dict[str, list[tuple[date, int]]]
) -> pd.DataFrame:
    """Normalise one day's CSV. Raises BhavcopyError when the format is not recognised."""
    try:
        raw = pd.read_csv(io.BytesIO(content), dtype=str, skipinitialspace=True)
    except (ValueError, pd.errors.ParserError) as exc:
        raise BhavcopyError("not a readable CSV file") from exc
    raw.columns = [str(column).strip() for column in raw.columns]
    found = set(raw.columns)
    wanted = set(symbols)
    if UDIFF_REQUIRED <= found:
        frame = _parse_udiff(raw, wanted)
    elif LEGACY_REQUIRED <= found:
        frame = _parse_legacy(raw, wanted, lots)
    else:
        closest = UDIFF_REQUIRED if "TradDt" in found else LEGACY_REQUIRED
        raise BhavcopyError(f"unrecognised format, missing columns: {sorted(closest - found)}")
    if frame.empty:
        raise BhavcopyError("file has no rows for the configured symbols")
    frame = frame.dropna(subset=["date", "symbol", "expiry", "close", "lot_size"])
    frame["lot_size"] = frame["lot_size"].astype(int)
    frame["option_type"] = frame["option_type"].where(frame["instrument"] == "OPT")
    for column in ("open", "high", "low", "volume", "oi", "oi_change"):
        frame[column] = frame[column].fillna(0.0)
    if frame["date"].nunique() != 1:
        raise BhavcopyError("file covers more than one trading date")
    return frame[COLUMNS].reset_index(drop=True)


def _read_file(path: Path) -> bytes:
    if path.suffix.lower() != ".zip":
        return path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise BhavcopyError("zip file must contain exactly one CSV")
        return archive.read(names[0])


def history_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "history" / "fo"


def store_day(data_dir: Path, frame: pd.DataFrame) -> Path:
    folder = history_dir(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    day = frame["date"].iloc[0]
    path = folder / f"{day.isoformat()}.parquet"
    tmp = path.with_suffix(".tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return path


@dataclass
class ImportResult:
    imported: list[date] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def import_files(
    paths: Iterable[Path],
    data_dir: Path,
    symbols: Iterable[str],
    lots: dict[str, list[tuple[date, int]]],
) -> ImportResult:
    """Import files, or every CSV and zip inside given folders. One bad file stops only itself."""
    result = ImportResult()
    symbols = list(symbols)
    files: list[Path] = []
    for path in map(Path, paths):
        if path.is_dir():
            files += sorted(p for p in path.iterdir() if p.suffix.lower() in (".csv", ".zip"))
        else:
            files.append(path)
    for file in files:
        try:
            frame = parse_bhavcopy(_read_file(file), symbols, lots)
        except (BhavcopyError, OSError, zipfile.BadZipFile) as exc:
            result.failed[file.name] = str(exc)
            continue
        store_day(data_dir, frame)
        result.imported.append(frame["date"].iloc[0])
    result.imported.sort()
    return result

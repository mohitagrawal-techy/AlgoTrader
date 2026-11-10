"""Upstox instrument master: the source of lot sizes and expiries."""

from __future__ import annotations

import gzip
import json
from datetime import date

import httpx
import pandas as pd

NSE_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
OPTION_COLUMNS = [
    "instrument_key",
    "underlying_symbol",
    "underlying_key",
    "underlying_type",
    "expiry",
    "strike",
    "option_type",
    "lot_size",
    "freeze_quantity",
    "tick_size",
    "weekly",
    "trading_symbol",
]

FUTURE_COLUMNS = ["instrument_key", "underlying_symbol", "expiry", "lot_size"]


def download_master(client: httpx.Client | None = None) -> list[dict]:
    http = client or httpx.Client(timeout=60, follow_redirects=True)
    try:
        response = http.get(NSE_MASTER_URL)
    finally:
        if client is None:
            http.close()
    response.raise_for_status()
    content = response.content
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    return json.loads(content)


def parse_option_contracts(rows: list[dict]) -> pd.DataFrame:
    """Keep NSE F&O option rows. Expiry epochs are converted to the IST calendar date."""
    options = [
        row
        for row in rows
        if row.get("segment") == "NSE_FO" and row.get("instrument_type") in ("CE", "PE")
    ]
    frame = pd.DataFrame.from_records(options)
    if frame.empty:
        return pd.DataFrame(columns=OPTION_COLUMNS)
    frame = frame.rename(columns={"strike_price": "strike", "instrument_type": "option_type"})
    frame["expiry"] = (
        pd.to_datetime(frame["expiry"], unit="ms", utc=True)
        .dt.tz_convert("Asia/Kolkata")
        .dt.date
    )
    for column in OPTION_COLUMNS:
        if column not in frame:
            frame[column] = None
    return frame[OPTION_COLUMNS].reset_index(drop=True)


def parse_future_contracts(rows: list[dict], symbols: set[str]) -> pd.DataFrame:
    """Keep NSE F&O futures for the given underlyings."""
    futures = [
        row
        for row in rows
        if row.get("segment") == "NSE_FO"
        and row.get("instrument_type") == "FUT"
        and row.get("underlying_symbol") in symbols
    ]
    frame = pd.DataFrame.from_records(futures, columns=FUTURE_COLUMNS)
    frame["expiry"] = (
        pd.to_datetime(frame["expiry"], unit="ms", utc=True)
        .dt.tz_convert("Asia/Kolkata")
        .dt.date
    )
    return frame


def underlying_key(contracts: pd.DataFrame, symbol: str) -> str:
    matches = contracts.loc[contracts["underlying_symbol"] == symbol, "underlying_key"].unique()
    if len(matches) != 1:
        raise ValueError(f"Expected one underlying key for {symbol}, found {list(matches)}")
    return str(matches[0])


def nearest_expiries(
    contracts: pd.DataFrame, symbol: str, today: date, count: int
) -> list[date]:
    expiries = contracts.loc[contracts["underlying_symbol"] == symbol, "expiry"].unique()
    return sorted(e for e in expiries if e >= today)[:count]

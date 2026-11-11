"""Parquet storage for snapshots, candles and dated contract specifications."""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from algoverse.data import options
from algoverse.data.candles import DAILY, DEFAULT_ALIASES, CandleStore, resolve_name


def _write_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)


class SnapshotStore:
    def __init__(self, data_dir: Path, aliases: dict[str, str] | None = None):
        self.data_dir = Path(data_dir)
        self.candles = CandleStore(self.data_dir)
        # Short names ("NIFTY", "INDIAVIX") by instrument key, so each instrument has one folder.
        self.aliases = {**DEFAULT_ALIASES, **(aliases or {})}

    def write_chain(self, symbol: str, ts: datetime, chain: pd.DataFrame, label: str) -> Path:
        """Store one snapshot. `label` (the expiry) keeps same-time snapshots apart."""
        return options.write_snapshot(self.data_dir, symbol, ts, chain, label)

    def read_chains(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        return options.read_chains(self.data_dir, symbol, start, end)

    def write_contracts(self, day: date, contracts: pd.DataFrame) -> Path:
        return options.write_contracts(self.data_dir, day, contracts)

    def read_contracts(self, as_of: date) -> pd.DataFrame | None:
        """Return the newest contract master dated on or before `as_of`."""
        return options.read_contracts(self.data_dir, as_of)

    def has_contracts(self, day: date) -> bool:
        return options.contracts_path(self.data_dir, day).exists()

    def upsert_candles(
        self, name: str, candles: pd.DataFrame, interval: str = DAILY
    ) -> list[Path]:
        """Add or replace candles. `name` is an instrument key ("NSE_INDEX|Nifty 50"), a short
        name ("NIFTY"), or "iv_<underlying>" for the daily ATM IV series."""
        target = resolve_name(name, self.aliases)
        if target.series:
            return self.candles.write_series(target.series, target.symbol, candles)
        return self.candles.write(target.instrument_key, candles, interval)

    def read_candles(
        self,
        name: str,
        interval: str = DAILY,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        target = resolve_name(name, self.aliases)
        if target.series:
            return self.candles.read_series(target.series, target.symbol)
        return self.candles.read(target.instrument_key, interval, start, end)

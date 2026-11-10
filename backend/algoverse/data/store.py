"""Parquet storage for snapshots, candles and dated contract specifications."""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd


def _write_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)


class SnapshotStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def write_chain(self, symbol: str, ts: datetime, chain: pd.DataFrame, label: str) -> Path:
        """Store one chain. `label` keeps same-time snapshots of different expiries apart."""
        path = (
            self.data_dir
            / "chains"
            / f"underlying={symbol}"
            / f"date={ts.date().isoformat()}"
            / f"{ts.strftime('%H%M%S')}_{label}.parquet"
        )
        _write_atomic(chain, path)
        return path

    def read_chains(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        glob = str(self.data_dir / "chains" / "*" / "*" / "*.parquet")
        if not list((self.data_dir / "chains").glob("*/*/*.parquet")):
            return pd.DataFrame()
        query = """
            SELECT * FROM read_parquet(?, hive_partitioning = true, union_by_name = true)
            WHERE underlying = ? AND date BETWEEN ? AND ?
            ORDER BY ts, expiry, strike, option_type
        """
        with duckdb.connect() as connection:
            return connection.execute(query, [glob, symbol, start, end]).df()

    def write_contracts(self, day: date, contracts: pd.DataFrame) -> Path:
        path = self.data_dir / "contracts" / f"{day.isoformat()}.parquet"
        _write_atomic(contracts, path)
        return path

    def read_contracts(self, as_of: date) -> pd.DataFrame | None:
        """Return the newest contract master dated on or before `as_of`."""
        candidates = sorted((self.data_dir / "contracts").glob("*.parquet"))
        eligible = [p for p in candidates if date.fromisoformat(p.stem) <= as_of]
        return pd.read_parquet(eligible[-1]) if eligible else None

    def upsert_candles(self, symbol: str, candles: pd.DataFrame) -> Path:
        path = self.data_dir / "candles" / f"{symbol}.parquet"
        merged = candles
        if path.exists():
            merged = pd.concat([pd.read_parquet(path), candles], ignore_index=True)
        merged = (
            merged.drop_duplicates(subset="ts", keep="last")
            .sort_values("ts")
            .reset_index(drop=True)
        )
        _write_atomic(merged, path)
        return path

    def read_candles(self, symbol: str) -> pd.DataFrame:
        path = self.data_dir / "candles" / f"{symbol}.parquet"
        return pd.read_parquet(path) if path.exists() else pd.DataFrame()

"""Historical data access. Strategies can only read through a point-in-time view."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from algoverse.backtest.bhavcopy import COLUMNS, history_dir

BAR_COLUMNS = ["ts", "open", "high", "low", "close", "volume", "oi"]
IST = "Asia/Kolkata"


class History:
    """Imported daily contract data. The engine reads this; strategies never do."""

    def __init__(self, data_dir: Path):
        self._folder = history_dir(data_dir)
        self._files = {
            date.fromisoformat(path.stem): path for path in sorted(self._folder.glob("*.parquet"))
        }
        self._bars: dict[str, pd.DataFrame] | None = None

    @property
    def sessions(self) -> list[date]:
        return sorted(self._files)

    def day(self, session: date) -> pd.DataFrame:
        return pd.read_parquet(self._files[session], columns=COLUMNS)

    def _build_bars(self) -> dict[str, pd.DataFrame]:
        """Continuous daily bars per symbol, from the nearest-expiry future on each day."""
        rows = []
        for session in self.sessions:
            day = self.day(session)
            futures = day[(day["instrument"] == "FUT") & (day["expiry"] >= session)]
            if not futures.empty:
                nearest = futures.sort_values("expiry").groupby("symbol", as_index=False).first()
                rows.append(nearest)
        if not rows:
            return {}
        frame = pd.concat(rows, ignore_index=True)
        frame["ts"] = pd.to_datetime(frame["date"]).dt.tz_localize(IST)
        return {
            str(symbol): group.sort_values("ts")[BAR_COLUMNS].reset_index(drop=True)
            for symbol, group in frame.groupby("symbol")
        }

    def bars(self, symbol: str) -> pd.DataFrame:
        if self._bars is None:
            self._bars = self._build_bars()
        return self._bars.get(symbol, pd.DataFrame(columns=BAR_COLUMNS))


class DataView:
    """Stands in for SnapshotStore during a replay. Reads stop before `as_of`."""

    def __init__(self, reference: dict[str, pd.DataFrame]):
        self.as_of: date | None = None
        self.data_dir = Path(".")
        self._reference = {name: self._indexed(frame) for name, frame in reference.items()}
        self._written: dict[str, list[pd.DataFrame]] = {}

    @staticmethod
    def _indexed(frame: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
        days = np.array(pd.to_datetime(frame["ts"]).dt.date.to_numpy(), dtype="datetime64[D]")
        return frame.reset_index(drop=True), days

    def read_candles(self, symbol: str) -> pd.DataFrame:
        if symbol in self._written:
            frame = pd.concat(self._written[symbol], ignore_index=True)
            return frame[pd.to_datetime(frame["ts"]).dt.date < self.as_of].reset_index(drop=True)
        if symbol not in self._reference:
            return pd.DataFrame()
        frame, days = self._reference[symbol]
        cutoff = np.searchsorted(days, np.datetime64(self.as_of, "D"), side="left")
        return frame.iloc[:cutoff]

    def upsert_candles(self, symbol: str, frame: pd.DataFrame) -> None:
        self._written.setdefault(symbol, []).append(frame)

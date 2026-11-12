"""Historical data access. Strategies can only read through a point-in-time view."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from algoverse.backtest.bhavcopy import COLUMNS, history_dir

BAR_COLUMNS = ["ts", "open", "high", "low", "close", "volume", "oi"]
IST = "Asia/Kolkata"
# Sessions filled from live contracts only, with the expiries Upstox could not serve.
INCOMPLETE_FILE = Path("history") / "upstox" / "incomplete.json"
INCOMPLETE_REASON = "data incomplete"


def load_incomplete(data_dir: Path) -> dict[str, dict[str, list[str]]]:
    try:
        return json.loads((Path(data_dir) / INCOMPLETE_FILE).read_text())
    except FileNotFoundError:
        return {}


class History:
    """Imported daily contract data. The engine reads this; strategies never do."""

    def __init__(self, data_dir: Path):
        self._folder = history_dir(data_dir)
        self._files = {
            date.fromisoformat(path.stem): path for path in sorted(self._folder.glob("*.parquet"))
        }
        self._bars: dict[str, pd.DataFrame] | None = None
        self._incomplete = load_incomplete(Path(data_dir))

    def entry_block(self, session: date, symbol: str) -> str | None:
        """Why no new trade may open on this underlying in this session, if it may not.

        A session filled after its nearest expiries had expired lacks those contracts. A
        strategy asking for the nearest expiry would get a later one, so it opens nothing;
        positions already open are still priced from the contracts that are there."""
        missing = self._incomplete.get(session.isoformat(), {}).get(symbol)
        if not missing:
            return None
        return f"{INCOMPLETE_REASON}: expiries {', '.join(missing)} were not available"

    @property
    def sessions(self) -> list[date]:
        return sorted(self._files)

    def day(self, session: date) -> pd.DataFrame:
        return pd.read_parquet(self._files[session], columns=COLUMNS)

    def intraday(self, session: date) -> pd.DataFrame:
        """The session's 5-minute option bars, or an empty frame when none were imported."""
        folder = self._folder / "intraday" / f"date={session.isoformat()}"
        files = sorted(folder.glob("*.parquet"))
        if not files:
            return pd.DataFrame()
        return pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)

    def has_intraday(self, session: date) -> bool:
        folder = self._folder / "intraday" / f"date={session.isoformat()}"
        return any(folder.glob("*.parquet"))

    def _build_bars(self) -> dict[str, pd.DataFrame]:
        """Continuous daily bars per symbol, from the nearest-expiry future on each day.

        Sessions built from option candle files have no futures; their index bar stands in.
        """
        rows = []
        for session in self.sessions:
            day = self.day(session)
            futures = day[(day["instrument"] == "FUT") & (day["expiry"] >= session)]
            if not futures.empty:
                nearest = futures.sort_values("expiry").groupby("symbol", as_index=False).first()
                rows.append(nearest)
            index = day[(day["instrument"] == "IDX") & ~day["symbol"].isin(futures["symbol"])]
            if not index.empty:
                rows.append(index.groupby("symbol", as_index=False).first())
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
        # Series written during the replay, one value per timestamp; a later write replaces it.
        self._written: dict[str, dict[pd.Timestamp, dict]] = {}

    @staticmethod
    def _indexed(frame: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
        days = np.array(pd.to_datetime(frame["ts"]).dt.date.to_numpy(), dtype="datetime64[D]")
        return frame.reset_index(drop=True), days

    def read_candles(self, symbol: str) -> pd.DataFrame:
        if symbol in self._written:
            rows = self._written[symbol]
            frame = pd.DataFrame(rows[ts] for ts in sorted(rows))
            return frame[pd.to_datetime(frame["ts"]).dt.date < self.as_of].reset_index(drop=True)
        if symbol not in self._reference:
            return pd.DataFrame()
        frame, days = self._reference[symbol]
        cutoff = np.searchsorted(days, np.datetime64(self.as_of, "D"), side="left")
        return frame.iloc[:cutoff]

    def upsert_candles(self, symbol: str, frame: pd.DataFrame) -> None:
        rows = self._written.setdefault(symbol, {})
        for row in frame.to_dict("records"):
            rows[pd.Timestamp(row["ts"])] = row

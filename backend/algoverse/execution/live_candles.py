"""Candles for rules during live paper trading: minute history plus today's minutes so far.

Rules on 5m/15m/1h candles read one-minute candles, and daily rules read today's candle. Past
sessions come from the candle store (filled by `algoverse backfill-minutes`); today's minutes
come from Upstox and are kept for a minute, so one decision cycle makes one request per
underlying. When Upstox cannot be reached the rules see no candles and report "not available",
which blocks the trade rather than guessing.
"""

from __future__ import annotations

import logging
import time as time_module
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from algoverse.brokers.base import AuthExpiredError, MarketDataError
from algoverse.data.candles import MINUTE

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
CACHE_SECONDS = 60
LOOKBACK_DAYS = 60


class LiveCandles:
    def __init__(
        self,
        market,
        store,
        clock: Callable[[], datetime] = lambda: datetime.now(IST),
        monotonic: Callable[[], float] = time_module.monotonic,
    ):
        self.market = market
        self.store = store
        self.clock = clock
        self.monotonic = monotonic
        self._today: dict[str, tuple[float, pd.DataFrame]] = {}

    def _key(self, symbol: str) -> str | None:
        return self.store.aliases.get(symbol)

    def _today_minutes(self, symbol: str) -> pd.DataFrame:
        cached = self._today.get(symbol)
        if cached and self.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        key = self._key(symbol)
        frame = pd.DataFrame()
        if key is not None:
            try:
                frame = self.market.intraday_candles(key, 1)
            except (AuthExpiredError, MarketDataError) as exc:
                log.warning("No intraday candles for %s: %s", symbol, exc)
        self._today[symbol] = (self.monotonic(), frame)
        return frame

    def minutes(self, symbol: str) -> pd.DataFrame:
        """One-minute candles that have finished, oldest first."""
        now = self.clock()
        earlier = self.store.read_candles(
            symbol, MINUTE, start=(now - timedelta(days=LOOKBACK_DAYS)).date(), end=now.date()
        )
        frames = [f for f in (earlier, self._today_minutes(symbol)) if not f.empty]
        if not frames:
            return pd.DataFrame()
        frame = pd.concat(frames, ignore_index=True)
        frame["ts"] = pd.to_datetime(frame["ts"]).dt.tz_convert(IST)
        frame = frame.drop_duplicates("ts", keep="last").sort_values("ts")
        finished = frame["ts"] + pd.Timedelta(minutes=1) <= now
        return frame[finished][["ts", "open", "high", "low", "close"]].reset_index(drop=True)

    def today_bar(self, symbol: str) -> dict | None:
        today = self._today_minutes(symbol)
        if today.empty:
            return None
        times = pd.to_datetime(today["ts"]).dt.tz_convert(IST)
        today = today[times.dt.date == self.clock().date()]
        if today.empty:
            return None
        today = today.sort_values("ts")
        return {
            "open": float(today["open"].iloc[0]),
            "high": float(today["high"].max()),
            "low": float(today["low"].min()),
            "close": float(today["close"].iloc[-1]),
        }

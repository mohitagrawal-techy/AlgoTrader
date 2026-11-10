"""Broker-neutral market data interface."""

from __future__ import annotations

from datetime import date
from typing import Protocol

import pandas as pd

CHAIN_COLUMNS = [
    "ts",
    "underlying_key",
    "expiry",
    "strike",
    "option_type",
    "instrument_key",
    "spot",
    "ltp",
    "close_price",
    "bid",
    "ask",
    "bid_qty",
    "ask_qty",
    "volume",
    "oi",
    "prev_oi",
    "broker_iv",
    "broker_delta",
    "broker_gamma",
    "broker_theta",
    "broker_vega",
    "broker_pop",
]

CANDLE_COLUMNS = ["ts", "open", "high", "low", "close", "volume", "oi"]


class MarketDataError(Exception):
    """A market data request failed."""


class AuthExpiredError(MarketDataError):
    """The broker session is missing or expired, and a fresh login is required."""


class PlanRequiredError(MarketDataError):
    """The session is valid, but the broker account lacks the plan this data needs."""


class MarketData(Protocol):
    def option_chain(self, underlying_key: str, expiry: date) -> pd.DataFrame:
        """Return one row per contract, with `CHAIN_COLUMNS`."""
        ...

    def daily_candles(self, instrument_key: str, start: date, end: date) -> pd.DataFrame:
        """Return daily bars with `CANDLE_COLUMNS`, oldest first."""
        ...

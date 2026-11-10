"""Read-only Upstox market data client. This module must never call order endpoints."""

from __future__ import annotations

import logging
import threading
import time as time_module
from collections import deque
from collections.abc import Callable
from datetime import date, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from algoverse.brokers.auth import TokenStore
from algoverse.brokers.base import (
    CANDLE_COLUMNS,
    CHAIN_COLUMNS,
    AuthExpiredError,
    MarketDataError,
    PlanRequiredError,
)

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
BASE_URL = "https://api.upstox.com"

# Upstox "Other Standard APIs" limits: (max calls, window seconds).
STANDARD_LIMITS = ((50, 1.0), (500, 60.0), (2000, 1800.0))
MAX_ATTEMPTS = 4
QUOTE_BATCH = 500
PLAN_REQUIRED_CODE = "UDAPI1149"


class RateLimiter:
    """Blocks until a call fits inside every sliding window."""

    def __init__(
        self,
        limits: tuple[tuple[int, float], ...] = STANDARD_LIMITS,
        clock: Callable[[], float] = time_module.monotonic,
        sleep: Callable[[float], None] = time_module.sleep,
    ):
        self._windows = [(limit, period, deque()) for limit, period in limits]
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            while True:
                now = self._clock()
                wait = 0.0
                for limit, period, calls in self._windows:
                    while calls and now - calls[0] >= period:
                        calls.popleft()
                    if len(calls) >= limit:
                        wait = max(wait, period - (now - calls[0]))
                if wait <= 0:
                    for _, _, calls in self._windows:
                        calls.append(now)
                    return
                self._sleep(wait)


class UpstoxMarketData:
    def __init__(
        self,
        tokens: TokenStore,
        client: httpx.Client | None = None,
        limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time_module.sleep,
    ):
        self._tokens = tokens
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=20)
        self._limiter = limiter or RateLimiter()
        self._sleep = sleep

    def _get(self, path: str, params: dict | None = None) -> object:
        token = self._tokens.current()
        if token is None:
            raise AuthExpiredError("No valid Upstox session. Log in again.")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token.access_token}",
        }
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._limiter.acquire()
            try:
                response = self._client.get(path, params=params, headers=headers)
            except httpx.TransportError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise MarketDataError(f"Upstox request failed: {path}") from exc
                self._sleep(2**attempt)
                continue
            if 400 <= response.status_code < 500 and PLAN_REQUIRED_CODE in response.text:
                raise PlanRequiredError(f"{path} needs an Upstox Plus plan.")
            if response.status_code == 401:
                raise AuthExpiredError("Upstox rejected the session. Log in again.")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == MAX_ATTEMPTS:
                    raise MarketDataError(f"Upstox returned {response.status_code}: {path}")
                self._sleep(2**attempt)
                continue
            if response.status_code != 200:
                raise MarketDataError(
                    f"Upstox returned {response.status_code} for {path}: {response.text[:300]}"
                )
            body = response.json()
            if body.get("status") != "success":
                raise MarketDataError(f"Upstox reported failure for {path}: {str(body)[:300]}")
            return body["data"]
        raise MarketDataError(f"Upstox request exhausted retries: {path}")

    def option_chain(self, underlying_key: str, expiry: date) -> pd.DataFrame:
        data = self._get(
            "/v2/option/chain",
            {"instrument_key": underlying_key, "expiry_date": expiry.isoformat()},
        )
        return parse_option_chain(data, ts=datetime.now(IST))

    def quotes(self, instrument_keys: list[str]) -> dict[str, dict]:
        """Latest quotes keyed by the requested instrument key. Unknown keys are left out."""
        results: dict[str, dict] = {}
        for start in range(0, len(instrument_keys), QUOTE_BATCH):
            batch = instrument_keys[start : start + QUOTE_BATCH]
            data = self._get("/v2/market-quote/quotes", {"instrument_key": ",".join(batch)})
            results.update({row["instrument_token"]: row for row in data.values()})
        return results

    def daily_candles(self, instrument_key: str, start: date, end: date) -> pd.DataFrame:
        key = quote(instrument_key, safe="")
        data = self._get(f"/v3/historical-candle/{key}/days/1/{end}/{start}")
        return parse_candles(data["candles"])

    def institutional_flows(
        self, category: str, data_types: list[str], interval: str
    ) -> dict[str, list[dict]]:
        """FII or DII activity by segment. `category` is "fii" or "dii", `interval` 1D or 1M."""
        return self._get(
            f"/v2/market/{category}", {"data_type": data_types, "interval": interval}
        )

    def expired_expiries(self, underlying_key: str) -> list[date]:
        """Past expiries Upstox still serves. Needs the Plus plan."""
        data = self._get("/v2/expired-instruments/expiries", {"instrument_key": underlying_key})
        return sorted(date.fromisoformat(day) for day in data)

    def expired_contracts(self, underlying_key: str, kind: str, expiry: date) -> list[dict]:
        """Contracts of one past expiry. `kind` is "option" or "future". Needs the Plus plan."""
        return self._get(
            f"/v2/expired-instruments/{kind}/contract",
            {"instrument_key": underlying_key, "expiry_date": expiry.isoformat()},
        )

    def expired_daily_candles(self, expired_key: str, start: date, end: date) -> pd.DataFrame:
        key = quote(expired_key, safe="")
        data = self._get(f"/v2/expired-instruments/historical-candle/{key}/day/{end}/{start}")
        return parse_candles(data["candles"])


def parse_option_chain(rows: list[dict], ts: datetime) -> pd.DataFrame:
    """Flatten the Upstox chain response to one row per contract."""
    records = []
    for row in rows:
        for option_type, side in (("CE", "call_options"), ("PE", "put_options")):
            contract = row.get(side)
            if not contract:
                continue
            market = contract.get("market_data") or {}
            greeks = contract.get("option_greeks") or {}
            records.append(
                {
                    "ts": ts,
                    "underlying_key": row["underlying_key"],
                    "expiry": date.fromisoformat(row["expiry"]),
                    "strike": float(row["strike_price"]),
                    "option_type": option_type,
                    "instrument_key": contract.get("instrument_key"),
                    "spot": row.get("underlying_spot_price"),
                    "ltp": market.get("ltp"),
                    "close_price": market.get("close_price"),
                    "bid": market.get("bid_price"),
                    "ask": market.get("ask_price"),
                    "bid_qty": market.get("bid_qty"),
                    "ask_qty": market.get("ask_qty"),
                    "volume": market.get("volume"),
                    "oi": market.get("oi"),
                    "prev_oi": market.get("prev_oi"),
                    "broker_iv": greeks.get("iv"),
                    "broker_delta": greeks.get("delta"),
                    "broker_gamma": greeks.get("gamma"),
                    "broker_theta": greeks.get("theta"),
                    "broker_vega": greeks.get("vega"),
                    "broker_pop": greeks.get("pop"),
                }
            )
    return pd.DataFrame.from_records(records, columns=CHAIN_COLUMNS)


def parse_candles(candles: list[list]) -> pd.DataFrame:
    """Convert Upstox candle arrays to a frame, oldest first."""
    frame = pd.DataFrame(candles, columns=CANDLE_COLUMNS)
    frame["ts"] = pd.to_datetime(frame["ts"])
    return frame.sort_values("ts", ignore_index=True)

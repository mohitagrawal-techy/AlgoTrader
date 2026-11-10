"""Keeps the live feed connected during market hours, and saves what it sends."""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import yaml
from pydantic import BaseModel, Field
from websockets.exceptions import WebSocketException

from algoverse.brokers.base import AuthExpiredError, MarketDataError, PlanRequiredError
from algoverse.brokers.proto import MarketDataFeed_pb2 as proto
from algoverse.brokers.upstox_feed import parse_market_status, parse_ticks
from algoverse.data.instruments import nearest_expiries, underlying_key
from algoverse.data.ticks import TickWriter, merge_pending, write_table
from algoverse.settings import Universe

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
LOGIN_WAIT_SECONDS = 30
CLOSED_WAIT_SECONDS = 30
PLAN_WAIT_SECONDS = 600
MAX_BACKOFF_SECONDS = 60
MAX_BUFFERED_ROWS = 200_000
FRESH_SECONDS = 15
RETRYABLE = (
    MarketDataError,
    WebSocketException,
    OSError,
    TimeoutError,
    ValueError,
    httpx.HTTPError,
)


class StreamConfig(BaseModel):
    enabled: bool = True
    mode: str = "full"
    max_instruments: int = Field(default=1900, ge=1, le=2000)
    expiries_per_underlying: int = Field(default=3, ge=1)
    strike_range: float = Field(default=0.15, gt=0)
    minutes_before_open: int = Field(default=15, ge=0)
    minutes_after_close: int = Field(default=15, ge=0)
    flush_seconds: float = Field(default=10.0, gt=0)
    live_seconds: float = Field(default=2.0, gt=0)


def load_stream_config(config_dir: Path) -> StreamConfig:
    with (Path(config_dir) / "stream.yaml").open() as file:
        return StreamConfig.model_validate(yaml.safe_load(file))


def live_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "live"


def build_subscription(
    market,
    universe: Universe,
    contracts: pd.DataFrame,
    futures: pd.DataFrame,
    config: StreamConfig,
    today: date,
) -> dict[str, dict]:
    """Instrument details by key. Indices and futures come first, then options nearest the money."""
    index_keys = {symbol: underlying_key(contracts, symbol) for symbol in universe.underlyings}
    quotes = market.quotes(list(index_keys.values()))
    chosen: dict[str, dict] = {}
    for symbol, key in {**universe.reference_indices, **index_keys}.items():
        chosen[key] = {"symbol": symbol, "instrument": "INDEX"}
    live_futures = futures[futures["expiry"] >= today].sort_values("expiry")
    for row in live_futures.itertuples():
        chosen[row.instrument_key] = {
            "symbol": row.underlying_symbol,
            "instrument": "FUT",
            "expiry": row.expiry,
        }
    candidates = []
    for symbol, key in index_keys.items():
        if key not in quotes:
            raise MarketDataError(f"No index quote for {symbol}")
        spot = float(quotes[key]["last_price"])
        expiries = nearest_expiries(contracts, symbol, today, config.expiries_per_underlying)
        options = contracts[
            (contracts["underlying_symbol"] == symbol)
            & contracts["expiry"].isin(expiries)
            & ((contracts["strike"] / spot - 1).abs() <= config.strike_range)
        ]
        for row in options.itertuples():
            candidates.append((abs(row.strike / spot - 1), symbol, row))
    candidates.sort(key=lambda item: item[0])
    for _, symbol, row in candidates[: max(config.max_instruments - len(chosen), 0)]:
        chosen[row.instrument_key] = {
            "symbol": symbol,
            "instrument": "OPT",
            "expiry": row.expiry,
            "strike": float(row.strike),
            "option_type": row.option_type,
        }
    return chosen


class FeedRecorder:
    """Turns feed messages into stored ticks, and publishes the latest prices."""

    def __init__(
        self,
        data_dir: Path,
        config: StreamConfig,
        instruments: dict[str, dict],
        clock: Callable[[], datetime],
    ):
        self.data_dir = Path(data_dir)
        self.config = config
        self.instruments = instruments
        self.clock = clock
        self.writer = TickWriter(data_dir)
        self.latest: dict[str, dict] = {}
        self.market_status: dict[str, str] = {}
        self.messages = 0
        self.last_tick: datetime | None = None
        start = clock()
        self._last_flush = start
        self._last_publish = start

    def on_message(self, message: proto.FeedResponse) -> None:
        now = self.clock()
        self.messages += 1
        if message.type == proto.market_info:
            self.market_status = parse_market_status(message)
            self.writer.append_status(now, self.market_status)
            return
        rows = []
        for tick in parse_ticks(message):
            details = self.instruments.get(tick["instrument_key"])
            if details is None:
                continue
            row = {**tick, **details, "received_at": now}
            rows.append(row)
            self.latest[tick["instrument_key"]] = row
        if rows:
            self.writer.add(rows)
            self.last_tick = now

    def on_idle(self) -> None:
        now = self.clock()
        seconds = (now - self._last_flush).total_seconds()
        if seconds >= self.config.flush_seconds or len(self.writer) >= MAX_BUFFERED_ROWS:
            self.writer.flush()
            self._last_flush = now
        if (now - self._last_publish).total_seconds() >= self.config.live_seconds:
            self.publish()
            self._last_publish = now

    def publish(self) -> None:
        if self.latest:
            write_table(live_dir(self.data_dir) / "latest.parquet", list(self.latest.values()))
        write_status(
            self.data_dir,
            {
                "state": "connected",
                "updated_at": self.clock().isoformat(),
                "subscribed": len(self.instruments),
                "market_status": self.market_status,
                "last_tick_at": self.last_tick.isoformat() if self.last_tick else None,
                "rows_written": self.writer.rows_written,
            },
        )

    def close(self) -> None:
        self.writer.flush()


def write_status(data_dir: Path, status: dict) -> None:
    path = live_dir(data_dir) / "status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(status))
    os.replace(tmp, path)


def read_status(data_dir: Path) -> dict | None:
    try:
        return json.loads((live_dir(data_dir) / "status.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _positive(value) -> float | None:
    """The feed sends 0 for a missing price."""
    return float(value) if value is not None and pd.notna(value) and value > 0 else None


def _live_side(row) -> dict:
    iv = _positive(row.iv)
    return {
        "bid": _positive(row.bid_1),
        "ask": _positive(row.ask_1),
        "ltp": _positive(row.ltp),
        "iv": iv,
        "delta": float(row.delta) if iv is not None else None,
        "oi": float(row.oi) if pd.notna(row.oi) else None,
        "volume": float(row.vtt) if pd.notna(row.vtt) else None,
    }


def live_chain(data_dir: Path, symbol: str, expiry: str, now: datetime) -> dict:
    """Latest feed prices for one expiry. `live` is false unless the feed is running now."""
    status = read_status(data_dir) or {"state": "off"}
    base = {"symbol": symbol, "expiry": expiry, "state": status["state"], "live": False, "rows": []}
    updated = status.get("updated_at")
    age = (now - datetime.fromisoformat(updated)).total_seconds() if updated else None
    path = live_dir(data_dir) / "latest.parquet"
    if status["state"] != "connected" or age is None or age > FRESH_SECONDS or not path.exists():
        return base
    frame = pd.read_parquet(path)
    frame = frame[frame["symbol"] == symbol]
    index = frame[frame["instrument"] == "INDEX"]
    options = frame[(frame["instrument"] == "OPT") & (frame["expiry"].astype(str) == expiry)]
    rows: dict[float, dict] = {}
    for row in options.itertuples():
        entry = rows.setdefault(float(row.strike), {"strike": float(row.strike)})
        entry["ce" if row.option_type == "CE" else "pe"] = _live_side(row)
    return base | {
        "live": bool(rows),
        "as_of": status.get("last_tick_at"),
        "spot": _positive(index["ltp"].iloc[0]) if not index.empty else None,
        "rows": sorted(rows.values(), key=lambda item: item["strike"]),
    }


class StreamService:
    def __init__(
        self,
        data_dir: Path,
        config: StreamConfig,
        feed,
        subscribe: Callable[[datetime], dict[str, dict]],
        is_session: Callable[[datetime], bool],
        has_login: Callable[[datetime], bool],
        clock: Callable[[], datetime] = lambda: datetime.now(IST),
        stop: threading.Event | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.config = config
        self.feed = feed
        self.subscribe = subscribe
        self.is_session = is_session
        self.has_login = has_login
        self.clock = clock
        self.stop = stop or threading.Event()

    def _status(self, state: str, detail: str | None = None) -> None:
        write_status(
            self.data_dir,
            {"state": state, "detail": detail, "updated_at": self.clock().isoformat()},
        )

    def _connect_once(self) -> int:
        instruments = self.subscribe(self.clock())
        recorder = FeedRecorder(self.data_dir, self.config, instruments, self.clock)
        try:
            self.feed.run(
                list(instruments),
                self.config.mode,
                recorder.on_message,
                recorder.on_idle,
                lambda: self.stop.is_set() or not self.is_session(self.clock()),
            )
        finally:
            recorder.close()
            self.messages = recorder.messages
        return recorder.messages

    def run(self) -> None:
        backoff = 1.0
        while not self.stop.is_set():
            now = self.clock()
            if not self.is_session(now):
                merged = merge_pending(self.data_dir)
                if merged:
                    log.info("Merged %s tick files", merged)
                self._status("closed")
                self.stop.wait(CLOSED_WAIT_SECONDS)
                continue
            if not self.has_login(now):
                self._status("login_needed", "Log in to Upstox to start the live feed.")
                self.stop.wait(LOGIN_WAIT_SECONDS)
                continue
            self.messages = 0
            try:
                self._status("connecting")
                self._connect_once()
                backoff = 1.0
            except AuthExpiredError as exc:
                self._status("login_needed", str(exc))
                self.stop.wait(LOGIN_WAIT_SECONDS)
            except PlanRequiredError as exc:
                self._status("error", str(exc))
                self.stop.wait(PLAN_WAIT_SECONDS)
            except RETRYABLE as exc:
                backoff = 1.0 if self.messages else min(backoff * 2, MAX_BACKOFF_SECONDS)
                log.warning("Live feed dropped: %s. Retrying in %.0fs", exc, backoff)
                self._status("reconnecting", type(exc).__name__)
                self.stop.wait(backoff)
        self._status("stopped")

"""Read-only client for the Upstox live market data feed."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime

import httpx
from websockets.sync.client import connect as websocket_connect

from algoverse.brokers.auth import TokenStore
from algoverse.brokers.base import AuthExpiredError, MarketDataError, PlanRequiredError
from algoverse.brokers.proto import MarketDataFeed_pb2 as proto
from algoverse.brokers.upstox import BASE_URL, PLAN_REQUIRED_CODE

log = logging.getLogger(__name__)
AUTHORIZE_PATH = "/v3/feed/market-data-feed/authorize"
SUBSCRIBE_BATCH = 500
MAX_MESSAGE_BYTES = 32 * 1024 * 1024
DEPTH_LEVELS = 5
POLL_SECONDS = 1.0


def _quote_levels(row: dict, levels) -> None:
    for number, level in enumerate(levels[:DEPTH_LEVELS], start=1):
        row[f"bid_qty_{number}"] = level.bidQ
        row[f"bid_{number}"] = level.bidP
        row[f"ask_qty_{number}"] = level.askQ
        row[f"ask_{number}"] = level.askP


def _ltpc(row: dict, ltpc) -> None:
    row["ltp"] = ltpc.ltp
    row["ltt"] = ltpc.ltt or None
    row["ltq"] = ltpc.ltq
    row["cp"] = ltpc.cp
    if ltpc.HasField("iep"):
        row["iep"] = ltpc.iep.value


def _greeks(row: dict, greeks) -> None:
    row["delta"] = greeks.delta
    row["theta"] = greeks.theta
    row["gamma"] = greeks.gamma
    row["vega"] = greeks.vega
    row["rho"] = greeks.rho


def _day_ohlc(row: dict, market_ohlc) -> None:
    for candle in market_ohlc.ohlc:
        if candle.interval == "1d":
            row["day_open"] = candle.open
            row["day_high"] = candle.high
            row["day_low"] = candle.low
            row["day_close"] = candle.close


def _market_full(row: dict, full) -> None:
    _ltpc(row, full.ltpc)
    _quote_levels(row, full.marketLevel.bidAskQuote)
    _greeks(row, full.optionGreeks)
    _day_ohlc(row, full.marketOHLC)
    row.update(
        atp=full.atp,
        vtt=full.vtt,
        oi=full.oi,
        iv=full.iv,
        tbq=full.tbq,
        tsq=full.tsq,
        rp=full.rp,
        ieq=full.ieq,
        iiq_total=full.iiqTotal,
        iiq_m=full.iiqM,
        cas_eligible=full.casEligible,
    )
    if full.iep:
        row["iep"] = full.iep


def parse_ticks(message: proto.FeedResponse) -> list[dict]:
    """One flat row per instrument. Fields the mode does not send are left out."""
    kind = proto.Type.Name(message.type)
    rows = []
    for key, feed in message.feeds.items():
        row = {
            "instrument_key": key,
            "feed_ts": message.currentTs or None,
            "message_type": kind,
            "request_mode": proto.RequestMode.Name(feed.requestMode),
        }
        union = feed.WhichOneof("FeedUnion")
        if union == "ltpc":
            _ltpc(row, feed.ltpc)
        elif union == "firstLevelWithGreeks":
            first = feed.firstLevelWithGreeks
            _ltpc(row, first.ltpc)
            _quote_levels(row, [first.firstDepth])
            _greeks(row, first.optionGreeks)
            row.update(vtt=first.vtt, oi=first.oi, iv=first.iv)
        elif union == "fullFeed":
            if feed.fullFeed.WhichOneof("FullFeedUnion") == "indexFF":
                _ltpc(row, feed.fullFeed.indexFF.ltpc)
                _day_ohlc(row, feed.fullFeed.indexFF.marketOHLC)
            else:
                _market_full(row, feed.fullFeed.marketFF)
        else:
            continue
        rows.append(row)
    return rows


def parse_market_status(message: proto.FeedResponse) -> dict[str, str]:
    return {
        segment: proto.MarketStatus.Name(status)
        for segment, status in message.marketInfo.segmentStatus.items()
    }


class UpstoxFeed:
    def __init__(
        self,
        tokens: TokenStore,
        client: httpx.Client | None = None,
        connect: Callable = websocket_connect,
    ):
        self._tokens = tokens
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=20)
        self._connect = connect

    def authorize(self, now: datetime | None = None) -> str:
        """A fresh connection address. Upstox allows each one to be used once."""
        token = self._tokens.current(now)
        if token is None:
            raise AuthExpiredError("No valid Upstox session. Log in again.")
        response = self._client.get(
            AUTHORIZE_PATH,
            headers={"Accept": "application/json", "Authorization": f"Bearer {token.access_token}"},
        )
        if PLAN_REQUIRED_CODE in response.text:
            raise PlanRequiredError("The live feed needs an Upstox Plus plan.")
        if response.status_code == 401:
            raise AuthExpiredError("Upstox rejected the session. Log in again.")
        if response.status_code != 200:
            raise MarketDataError(f"Upstox feed authorisation returned {response.status_code}")
        return response.json()["data"]["authorized_redirect_uri"]

    def run(
        self,
        keys: list[str],
        mode: str,
        on_message: Callable[[proto.FeedResponse], None],
        on_idle: Callable[[], None],
        should_stop: Callable[[], bool],
    ) -> None:
        """Stream until `should_stop` is true. A dropped connection raises, so callers retry."""
        with self._connect(self.authorize(), max_size=MAX_MESSAGE_BYTES, open_timeout=20) as socket:
            for start in range(0, len(keys), SUBSCRIBE_BATCH):
                request = {
                    "guid": uuid.uuid4().hex,
                    "method": "sub",
                    "data": {"mode": mode, "instrumentKeys": keys[start : start + SUBSCRIBE_BATCH]},
                }
                socket.send(json.dumps(request).encode())  # Upstox requires a binary frame.
            while not should_stop():
                try:
                    raw = socket.recv(timeout=POLL_SECONDS)
                except TimeoutError:
                    on_idle()
                    continue
                if isinstance(raw, str):
                    log.warning("Upstox feed sent text: %s", raw[:300])
                    continue
                message = proto.FeedResponse()
                message.ParseFromString(raw)
                on_message(message)
                on_idle()

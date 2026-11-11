import json
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import pyarrow.parquet as pq
import pytest
import respx
from algoverse.api.app import create_app
from algoverse.brokers.auth import StoredToken, TokenStore
from algoverse.brokers.base import AuthExpiredError, PlanRequiredError
from algoverse.brokers.proto import MarketDataFeed_pb2 as proto
from algoverse.brokers.upstox import BASE_URL
from algoverse.brokers.upstox_feed import AUTHORIZE_PATH, UpstoxFeed, parse_ticks
from algoverse.data.stream import (
    FeedRecorder,
    StreamConfig,
    StreamService,
    build_subscription,
    live_chain,
    load_stream_config,
    split_connections,
    write_status,
)
from algoverse.data.ticks import (
    DAY_FILE,
    MANIFEST,
    TickWriter,
    merge_day,
    read_ticks,
    stored_days,
    ticks_dir,
    write_table,
)
from algoverse.settings import Settings
from fastapi.testclient import TestClient

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 12, 10, 0, tzinfo=IST)
KEY = "NSE_FO|1"
INDEX = "NSE_INDEX|Nifty 50"
EXPIRY = date(2026, 11, 17)
DETAILS = {
    KEY: {
        "symbol": "NIFTY",
        "instrument": "OPT",
        "expiry": EXPIRY,
        "strike": 24000.0,
        "option_type": "CE",
    },
    INDEX: {"symbol": "NIFTY", "instrument": "INDEX"},
}
CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def option_message(ltp=100.0, kind=proto.live_feed):
    message = proto.FeedResponse(type=kind, currentTs=1794306421481)
    full = message.feeds[KEY].fullFeed.marketFF
    message.feeds[KEY].requestMode = proto.full_d5
    full.ltpc.ltp = ltp
    full.ltpc.ltt = 1794218999689
    full.ltpc.ltq = 65
    full.ltpc.cp = 90.0
    for level in range(5):
        full.marketLevel.bidAskQuote.add(bidQ=65, bidP=ltp - level, askQ=130, askP=ltp + level)
    full.optionGreeks.delta = 0.5
    full.marketOHLC.ohlc.add(interval="1d", open=80, high=120, low=70, close=ltp, vol=1, ts=1)
    full.vtt = 6500
    full.oi = 13000.0
    full.iv = 0.17
    return message


def index_message(ltp=24000.0):
    message = proto.FeedResponse(type=proto.live_feed, currentTs=1794306421481)
    message.feeds[INDEX].fullFeed.indexFF.ltpc.ltp = ltp
    return message


def status_message():
    message = proto.FeedResponse(type=proto.market_info, currentTs=1794306421481)
    message.marketInfo.segmentStatus["NSE_FO"] = proto.NORMAL_OPEN
    return message


@pytest.fixture
def tokens(tmp_path):
    store = TokenStore(tmp_path / "token.json")
    store.save(StoredToken("secret-token", datetime.now(IST) + timedelta(hours=1)))
    return store


def test_full_feed_is_flattened_with_depth_and_greeks():
    row = parse_ticks(option_message())[0]
    assert row["ltp"] == 100.0 and row["vtt"] == 6500 and row["oi"] == 13000.0
    assert row["bid_1"] == 100.0 and row["ask_5"] == 104.0 and row["ask_qty_3"] == 130
    assert row["delta"] == 0.5 and row["iv"] == pytest.approx(0.17)
    assert row["day_high"] == 120 and row["request_mode"] == "full_d5"


def test_index_and_ltp_only_feeds_are_understood():
    assert parse_ticks(index_message())[0]["ltp"] == 24000.0
    message = proto.FeedResponse(type=proto.live_feed)
    message.feeds[KEY].ltpc.ltp = 5.0
    assert parse_ticks(message)[0]["ltp"] == 5.0


def recorder(data_dir, clock=lambda: NOW):
    return FeedRecorder(data_dir, StreamConfig(), DETAILS, clock)


def test_ticks_are_stored_with_contract_details(tmp_path):
    feed = recorder(tmp_path)
    feed.on_message(option_message())
    feed.on_message(index_message())
    feed.close()
    ticks = read_ticks(tmp_path, NOW.date(), NOW.date())
    assert len(ticks) == 2
    option = ticks[ticks["instrument_key"] == KEY].iloc[0]
    assert option["symbol"] == "NIFTY" and option["strike"] == 24000.0
    assert pd.Timestamp(option["expiry"]).date() == EXPIRY
    assert len(read_ticks(tmp_path, NOW.date(), NOW.date(), "BANKNIFTY")) == 0


def test_unknown_instruments_are_ignored_and_status_is_logged(tmp_path):
    feed = FeedRecorder(tmp_path, StreamConfig(), {}, lambda: NOW)
    feed.on_message(option_message())
    feed.on_message(status_message())
    feed.close()
    assert len(read_ticks(tmp_path, NOW.date(), NOW.date())) == 0
    log = ticks_dir(tmp_path) / "date=2026-11-12" / "market_status.jsonl"
    assert json.loads(log.read_text())["status"] == {"NSE_FO": "NORMAL_OPEN"}


def test_ticks_flush_on_the_timer_not_every_message(tmp_path):
    current = [NOW]
    feed = recorder(tmp_path, lambda: current[0])
    feed.on_message(option_message())
    feed.on_idle()
    assert stored_days(tmp_path) == []
    current[0] = NOW + timedelta(seconds=11)
    feed.on_idle()
    assert stored_days(tmp_path)[0]["rows"] == 1


def seed_parts(data_dir, count=3):
    writer = TickWriter(data_dir)
    for index in range(count):
        at = NOW + timedelta(seconds=count - index)
        writer.add([{"received_at": at, "instrument_key": f"K{index % 2}", "ltp": float(index)}])
        writer.flush()
    return ticks_dir(data_dir) / "date=2026-11-12"


def test_merge_makes_one_sorted_day_file(tmp_path):
    folder = seed_parts(tmp_path)
    assert merge_day(folder) == 3
    assert [p.name for p in folder.glob("*.parquet")] == [DAY_FILE]
    frame = pq.read_table(folder / DAY_FILE).to_pandas()
    assert list(frame["instrument_key"]) == ["K0", "K0", "K1"]
    assert frame["received_at"].iloc[0] < frame["received_at"].iloc[1]


def test_later_merge_keeps_earlier_ticks(tmp_path):
    folder = seed_parts(tmp_path)
    merge_day(folder)
    seed_parts(tmp_path, count=2)
    merge_day(folder)
    assert stored_days(tmp_path)[0]["rows"] == 5


def test_merge_interrupted_after_writing_does_not_double_count(tmp_path):
    folder = seed_parts(tmp_path)
    parts = sorted(p.name for p in folder.glob("part-*.parquet"))
    merge_day(folder)
    # Rebuild the state a crash leaves: parts remain and the merge finished but was not cleaned up.
    day = folder / DAY_FILE
    seed = pq.read_table(day)
    for name in parts:
        pq.write_table(seed.slice(0, 1), folder / name)
    (folder / MANIFEST).write_text(json.dumps(parts))
    assert merge_day(folder) == 0
    assert stored_days(tmp_path)[0]["rows"] == 3


class FakeSocket:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def send(self, data):
        self.sent.append(data)

    def recv(self, timeout):
        if not self.frames:
            raise TimeoutError
        item = self.frames.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def authorize_route():
    return respx.get(f"{BASE_URL}{AUTHORIZE_PATH}").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "data": {"authorized_redirect_uri": "wss://feed/one"}},
        )
    )


@respx.mock
def test_subscribes_in_binary_batches_and_decodes(tokens):
    authorize_route()
    socket = FakeSocket([option_message().SerializeToString(), "a text frame"])
    urls = []

    def connect(url, **_):
        urls.append(url)
        return socket

    feed = UpstoxFeed(tokens, client=httpx.Client(base_url=BASE_URL), connect=connect)
    received, polls = [], []
    keys = [f"K{i}" for i in range(501)]

    def stop():
        polls.append(1)
        return len(polls) > 4

    feed.run(keys, "full", received.append, lambda: None, stop)
    assert urls == ["wss://feed/one"]
    assert len(socket.sent) == 2 and all(isinstance(frame, bytes) for frame in socket.sent)
    first = json.loads(socket.sent[0])
    assert first["method"] == "sub" and first["data"]["mode"] == "full"
    assert len(first["data"]["instrumentKeys"]) == 500
    assert len(received) == 1 and received[0].feeds[KEY].fullFeed.marketFF.ltpc.ltp == 100.0


@respx.mock
def test_expired_login_and_missing_plan_are_reported(tokens):
    feed = UpstoxFeed(tokens, client=httpx.Client(base_url=BASE_URL))
    route = respx.get(f"{BASE_URL}{AUTHORIZE_PATH}")
    route.mock(return_value=httpx.Response(401, json={"status": "error"}))
    with pytest.raises(AuthExpiredError):
        feed.authorize()
    route.mock(return_value=httpx.Response(403, text="UDAPI1149"))
    with pytest.raises(PlanRequiredError):
        feed.authorize()


class FakeFeed:
    def __init__(self, stop, outcomes):
        self.stop = stop
        self.outcomes = list(outcomes)
        self.calls = 0

    def run(self, keys, mode, on_message, on_idle, should_stop):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if not self.outcomes:
            self.stop.set()
        if isinstance(outcome, Exception):
            raise outcome
        on_message(outcome)


def service(data_dir, feed, stop, session=True, login=True):
    return StreamService(
        data_dir,
        StreamConfig(),
        feed,
        lambda now: DETAILS,
        lambda now: session,
        lambda now: login,
        clock=lambda: NOW,
        stop=stop,
    )


def test_service_reconnects_and_keeps_received_ticks(tmp_path):
    stop = threading.Event()
    feed = FakeFeed(stop, [OSError("dropped"), option_message()])
    stop_wait = stop.wait
    stop.wait = lambda timeout=None: stop_wait(0)
    service(tmp_path, feed, stop).run()
    assert feed.calls == 2
    assert stored_days(tmp_path)[0]["rows"] == 1


def test_service_merges_days_when_the_market_is_closed(tmp_path):
    folder = seed_parts(tmp_path)
    stop = threading.Event()
    stop.wait = lambda timeout=None: stop.set()
    service(tmp_path, FakeFeed(stop, [option_message()]), stop, session=False).run()
    assert [p.name for p in folder.glob("*.parquet")] == [DAY_FILE]


def test_service_waits_for_a_login(tmp_path):
    stop = threading.Event()
    states = []

    class Recording(StreamService):
        def _status(self, state, detail=None):
            states.append(state)

    stop.wait = lambda timeout=None: stop.set()
    feed = FakeFeed(stop, [option_message()])
    Recording(tmp_path, StreamConfig(), feed, lambda n: DETAILS, lambda n: True, lambda n: False,
              clock=lambda: NOW, stop=stop).run()
    assert states[0] == "login_needed" and feed.calls == 0


class QuoteMarket:
    def quotes(self, keys):
        return {key: {"last_price": 24000.0} for key in keys}


def subscription_tables():
    contracts = pd.DataFrame(
        [
            {
                "instrument_key": f"O{strike}",
                "underlying_symbol": "NIFTY",
                "underlying_key": INDEX,
                "expiry": EXPIRY,
                "strike": float(strike),
                "option_type": "CE",
            }
            for strike in (24000, 24500, 25000, 40000)
        ]
    )
    futures = pd.DataFrame(
        [
            {
                "instrument_key": "F1",
                "underlying_symbol": "NIFTY",
                "expiry": EXPIRY,
                "lot_size": 65,
            },
            {
                "instrument_key": "F0",
                "underlying_symbol": "NIFTY",
                "expiry": date(2026, 10, 27),
                "lot_size": 65,
            },
        ]
    )
    universe = SimpleNamespace(
        underlyings=["NIFTY"], reference_indices={"INDIAVIX": "NSE_INDEX|VIX"}
    )
    return universe, contracts, futures


def test_subscription_covers_indices_futures_and_nearby_strikes():
    universe, contracts, futures = subscription_tables()
    config = StreamConfig()
    chosen = build_subscription(QuoteMarket(), universe, contracts, futures, config, NOW.date())
    assert set(chosen) == {INDEX, "NSE_INDEX|VIX", "F1", "O24000", "O24500", "O25000"}
    assert chosen[INDEX]["symbol"] == "NIFTY" and chosen["NSE_INDEX|VIX"]["symbol"] == "INDIAVIX"
    assert chosen["O24500"]["strike"] == 24500.0


def test_subscription_cap_keeps_strikes_nearest_the_money():
    universe, contracts, futures = subscription_tables()
    config = StreamConfig(connections=[{"mode": "full", "max_instruments": 4}])
    chosen = build_subscription(QuoteMarket(), universe, contracts, futures, config, NOW.date())
    assert len(chosen) == 4 and "O24000" in chosen and "O25000" not in chosen


def test_empty_range_subscribes_every_strike_and_expiry():
    universe, contracts, futures = subscription_tables()
    later = contracts.assign(instrument_key=lambda f: "L" + f["instrument_key"],
                             expiry=date(2027, 3, 30))
    config = StreamConfig(expiries_per_underlying=None, strike_range=None)
    chosen = build_subscription(
        QuoteMarket(), universe, pd.concat([contracts, later]), futures, config, NOW.date()
    )
    assert {"O40000", "LO40000", "LO24000"} <= set(chosen)


def test_far_expiries_can_be_left_out():
    universe, contracts, futures = subscription_tables()
    later = contracts.assign(
        instrument_key=lambda f: "L" + f["instrument_key"], expiry=NOW.date() + timedelta(days=600)
    )
    config = StreamConfig(expiries_per_underlying=None, strike_range=None, max_days_to_expiry=500)
    chosen = build_subscription(
        QuoteMarket(), universe, pd.concat([contracts, later]), futures, config, NOW.date()
    )
    assert "O24000" in chosen and not any(key.startswith("L") for key in chosen)


def test_connections_fill_in_priority_order():
    universe, contracts, futures = subscription_tables()
    config = StreamConfig(
        connections=[
            {"mode": "full", "max_instruments": 4},
            {"mode": "option_greeks", "max_instruments": 10},
        ],
        strike_range=None,
    )
    chosen = build_subscription(QuoteMarket(), universe, contracts, futures, config, NOW.date())
    groups = split_connections(chosen, config)
    assert [mode for mode, _ in groups] == ["full", "option_greeks"]
    assert groups[0][1] == ["NSE_INDEX|VIX", INDEX, "F1", "O24000"]
    assert groups[1][1] == ["O24500", "O25000", "O40000"]


@pytest.mark.parametrize(
    "connections",
    [
        [{"mode": "full", "max_instruments": 2001}],
        [{"mode": "full", "max_instruments": 1600}, {"mode": "ltpc", "max_instruments": 10}],
        [{"mode": "full", "max_instruments": 10}, {"mode": "full", "max_instruments": 10}],
        [{"mode": m, "max_instruments": 1} for m in ("full", "ltpc", "option_greeks")],
    ],
)
def test_configs_beyond_upstox_limits_are_refused(connections):
    with pytest.raises(ValueError):
        StreamConfig(connections=connections)


def test_config_file_stays_inside_upstox_limits():
    config = load_stream_config(CONFIG_DIR)
    assert [c.mode for c in config.connections] == ["full", "option_greeks"]
    assert config.max_instruments == 3500
    assert config.expiries_per_underlying is None and config.strike_range is None
    assert config.max_days_to_expiry == 500
    assert (config.minutes_before_open, config.minutes_after_close) == (15, 15)


def test_two_connections_share_one_recorder(tmp_path):
    stop = threading.Event()
    modes = []

    class TwoFeeds:
        def run(self, keys, mode, on_message, on_idle, should_stop):
            modes.append((mode, keys))
            if mode == "full":
                on_message(option_message())
                stop.set()
            else:
                while not should_stop():
                    time.sleep(0.01)

    config = StreamConfig(
        connections=[
            {"mode": "full", "max_instruments": 1},
            {"mode": "option_greeks", "max_instruments": 5},
        ]
    )
    StreamService(tmp_path, config, TwoFeeds(), lambda now: DETAILS, lambda now: True,
                  lambda now: True, clock=lambda: NOW, stop=stop).run()
    assert sorted(m for m, _ in modes) == ["full", "option_greeks"]
    assert sum(len(keys) for _, keys in modes) == len(DETAILS)
    assert stored_days(tmp_path)[0]["rows"] == 1


def test_one_dropped_connection_closes_the_other_and_reconnects(tmp_path):
    stop = threading.Event()
    stop_wait = stop.wait
    stop.wait = lambda timeout=None: stop_wait(0)
    calls = []

    class Flaky:
        def run(self, keys, mode, on_message, on_idle, should_stop):
            calls.append(mode)
            if len(calls) <= 2 and mode == "full":
                raise OSError("dropped")
            if len(calls) > 2:
                stop.set()
            while not should_stop():
                time.sleep(0.01)

    config = StreamConfig(
        connections=[
            {"mode": "full", "max_instruments": 1},
            {"mode": "option_greeks", "max_instruments": 5},
        ]
    )
    StreamService(tmp_path, config, Flaky(), lambda now: DETAILS, lambda now: True,
                  lambda now: True, clock=lambda: NOW, stop=stop).run()
    assert len(calls) == 4


def publish(data_dir, state="connected", at=NOW):
    write_table(
        data_dir / "live" / "latest.parquet",
        [
            {"received_at": NOW, "instrument_key": INDEX, "ltp": 24010.0, **DETAILS[INDEX]},
            {
                "received_at": NOW,
                "instrument_key": KEY,
                "ltp": 101.0,
                "bid_1": 100.5,
                "ask_1": 0.0,
                "iv": 0.17,
                "delta": 0.5,
                "gamma": 0.0007,
                "theta": -14.6,
                "vega": 14.7,
                "oi": 13000.0,
                "vtt": 6500,
                **DETAILS[KEY],
            },
        ],
    )
    stamp = at.isoformat()
    write_status(data_dir, {"state": state, "updated_at": stamp, "last_tick_at": stamp})


def test_live_chain_returns_current_prices(tmp_path):
    publish(tmp_path)
    live = live_chain(tmp_path, "NIFTY", "2026-11-17", NOW + timedelta(seconds=3))
    assert live["live"] is True and live["spot"] == 24010.0
    side = live["rows"][0]["ce"]
    assert side["ltp"] == 101.0 and side["bid"] == 100.5 and side["ask"] is None
    assert side["volume"] == 6500.0
    assert (side["gamma"], side["theta"], side["vega"]) == (0.0007, -14.6, 14.7)


@pytest.mark.parametrize(
    ("state", "age"), [("connected", 60), ("closed", 1), ("login_needed", 1)]
)
def test_stale_or_stopped_feed_is_not_called_live(tmp_path, state, age):
    publish(tmp_path, state=state)
    live = live_chain(tmp_path, "NIFTY", "2026-11-17", NOW + timedelta(seconds=age))
    assert live["live"] is False and live["rows"] == []


def test_live_endpoint_works_before_the_feed_has_run(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, ai_enabled=False)
    response = TestClient(create_app(settings)).get(
        "/api/market/live", params={"symbol": "NIFTY", "expiry": "2026-11-17"}
    )
    assert response.status_code == 200
    assert response.json()["live"] is False and response.json()["state"] == "off"

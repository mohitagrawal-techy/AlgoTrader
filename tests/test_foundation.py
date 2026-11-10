import re
import stat
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import pytest
import respx
from algoverse.api.app import create_app
from algoverse.brokers.auth import StoredToken, TokenStore, authorize_url, token_expiry
from algoverse.brokers.base import AuthExpiredError, MarketDataError
from algoverse.brokers.upstox import BASE_URL, RateLimiter, UpstoxMarketData
from algoverse.data.calendar import MarketCalendar
from algoverse.data.collector import backfill_daily, collect_snapshots
from algoverse.data.instruments import nearest_expiries, parse_option_contracts, underlying_key
from algoverse.data.store import SnapshotStore
from algoverse.settings import Settings, Universe
from fastapi.testclient import TestClient

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 12, 10, 0, tzinfo=IST)
BACKEND = Path(__file__).resolve().parents[1] / "backend"


def option_row(**overrides):
    row = {
        "weekly": False,
        "segment": "NSE_FO",
        "exchange": "NSE",
        "expiry": 1706207399000,
        "instrument_type": "CE",
        "underlying_symbol": "IDEA",
        "instrument_key": "NSE_FO|36708",
        "lot_size": 80000,
        "freeze_quantity": 1600000.0,
        "underlying_key": "NSE_EQ|INE669E01016",
        "tick_size": 5.0,
        "underlying_type": "EQUITY",
        "trading_symbol": "IDEA 22 CE 25 JAN 24",
        "strike_price": 22.0,
    }
    return row | overrides


CHAIN_RESPONSE = {
    "status": "success",
    "data": [
        {
            "expiry": "2026-11-17",
            "pcr": 1.1,
            "strike_price": 24000,
            "underlying_key": "NSE_INDEX|Nifty 50",
            "underlying_spot_price": 24100.5,
            "call_options": {
                "instrument_key": "NSE_FO|1",
                "market_data": {
                    "ltp": 200.0,
                    "volume": 10,
                    "oi": 100,
                    "close_price": 190.0,
                    "bid_price": 199.0,
                    "bid_qty": 65,
                    "ask_price": 201.0,
                    "ask_qty": 130,
                    "prev_oi": 90,
                },
                "option_greeks": {
                    "vega": 1.0,
                    "theta": -2.0,
                    "gamma": 0.001,
                    "delta": 0.6,
                    "iv": 14.0,
                    "pop": 55.0,
                },
            },
            "put_options": {"instrument_key": "NSE_FO|2", "market_data": {}, "option_greeks": {}},
        }
    ],
}


@pytest.fixture
def tokens(tmp_path):
    store = TokenStore(tmp_path / "token.json")
    store.save(StoredToken("secret-token", datetime.now(IST) + timedelta(hours=1)))
    return store


def client_for(tokens):
    sleeps = []
    client = httpx.Client(base_url=BASE_URL)
    return UpstoxMarketData(tokens, client=client, sleep=sleeps.append), sleeps


@pytest.mark.parametrize(
    ("issued", "expected"),
    [
        (datetime(2026, 11, 12, 10, 0, tzinfo=IST), datetime(2026, 11, 13, 3, 30, tzinfo=IST)),
        (datetime(2026, 11, 12, 3, 29, tzinfo=IST), datetime(2026, 11, 12, 3, 30, tzinfo=IST)),
        (datetime(2026, 11, 12, 3, 30, tzinfo=IST), datetime(2026, 11, 13, 3, 30, tzinfo=IST)),
    ],
)
def test_token_expiry(issued, expected):
    assert token_expiry(issued) == expected


def test_token_file_is_private_and_expiry_is_honoured(tmp_path):
    store = TokenStore(tmp_path / "token.json")
    expires = NOW + timedelta(hours=1)
    store.save(StoredToken("abc", expires))
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.current(NOW).access_token == "abc"
    assert store.current(expires) is None


def test_authorize_url_contains_required_parameters():
    settings = Settings(_env_file=None, upstox_api_key="key")
    url = authorize_url(settings, "s1")
    assert "client_id=key" in url and "response_type=code" in url and "state=s1" in url


def test_callback_rejects_unknown_state(tmp_path):
    settings = Settings(_env_file=None, upstox_api_key="key", data_dir=tmp_path, ai_enabled=False)
    client = TestClient(create_app(settings))
    assert client.get("/auth/callback", params={"code": "c", "state": "forged"}).status_code == 400
    assert client.get("/api/health").json()["broker_session_valid"] is False


@respx.mock
def test_login_round_trip_stores_token(tmp_path):
    respx.post("https://api.upstox.com/v2/login/authorization/token").respond(
        200, json={"access_token": "fresh"}
    )
    settings = Settings(_env_file=None, upstox_api_key="key", data_dir=tmp_path, ai_enabled=False)
    client = TestClient(create_app(settings), follow_redirects=False)
    location = client.get("/auth/login").headers["location"]
    state = re.search(r"state=([^&]+)", location).group(1)
    assert client.get("/auth/callback", params={"code": "c", "state": state}).status_code == 200
    assert client.get("/api/health").json()["broker_session_valid"] is True
    assert client.get("/auth/callback", params={"code": "c", "state": state}).status_code == 400


def test_calendar():
    calendar = MarketCalendar({date(2026, 11, 10)})
    assert calendar.is_open(datetime(2026, 11, 12, 9, 15, tzinfo=IST))
    assert calendar.is_open(datetime(2026, 11, 12, 15, 30, tzinfo=IST))
    assert not calendar.is_open(datetime(2026, 11, 12, 15, 31, tzinfo=IST))
    assert not calendar.is_open(datetime(2026, 11, 10, 10, 0, tzinfo=IST))
    assert not calendar.is_open(datetime(2026, 11, 14, 10, 0, tzinfo=IST))
    assert calendar.is_open(datetime(2026, 11, 12, 4, 30, tzinfo=ZoneInfo("UTC")))


def test_parse_contracts_uses_ist_expiry_date_and_filters_non_options():
    frame = parse_option_contracts(
        [option_row(), option_row(instrument_type="FUT"), option_row(segment="NSE_EQ")]
    )
    assert len(frame) == 1
    assert frame.loc[0, "expiry"] == date(2024, 1, 25)
    assert frame.loc[0, "strike"] == 22.0


def contracts():
    expiries = [date(2026, 11, 9), date(2026, 11, 17), date(2026, 11, 23), date(2026, 12, 29)]
    return pd.DataFrame(
        {
            "underlying_symbol": "NIFTY",
            "underlying_key": "NSE_INDEX|Nifty 50",
            "expiry": expiries,
        }
    )


def test_nearest_expiries_excludes_past():
    assert nearest_expiries(contracts(), "NIFTY", date(2026, 11, 12), 2) == [
        date(2026, 11, 17),
        date(2026, 11, 23),
    ]


def test_underlying_key_rejects_unknown():
    assert underlying_key(contracts(), "NIFTY") == "NSE_INDEX|Nifty 50"
    with pytest.raises(ValueError):
        underlying_key(contracts(), "NOPE")


@respx.mock
def test_option_chain_parses_documented_shape(tokens):
    route = respx.get(f"{BASE_URL}/v2/option/chain").respond(200, json=CHAIN_RESPONSE)
    market, _ = client_for(tokens)
    chain = market.option_chain("NSE_INDEX|Nifty 50", date(2026, 11, 17))
    assert route.calls.last.request.headers["authorization"] == "Bearer secret-token"
    assert route.calls.last.request.url.params["expiry_date"] == "2026-11-17"
    call = chain[chain["option_type"] == "CE"].iloc[0]
    assert (call["strike"], call["bid"], call["ask"], call["oi"]) == (24000.0, 199.0, 201.0, 100)
    assert call["broker_iv"] == 14.0
    assert call["spot"] == 24100.5
    assert pd.isna(chain[chain["option_type"] == "PE"].iloc[0]["bid"])


@respx.mock
def test_candles_url_encodes_key_and_sorts(tokens):
    route = respx.get(
        url__regex=r".*/v3/historical-candle/NSE_INDEX%7CIndia%20VIX/days/1/2026-11-12/2026-11-01$"
    ).respond(
        200,
        json={
            "status": "success",
            "data": {
                "candles": [
                    ["2026-11-03T00:00:00+05:30", 12, 13, 11, 12.5, 0, 0],
                    ["2026-11-02T00:00:00+05:30", 11, 12, 10, 11.5, 0, 0],
                ]
            },
        },
    )
    market, _ = client_for(tokens)
    candles = market.daily_candles("NSE_INDEX|India VIX", date(2026, 11, 1), date(2026, 11, 12))
    assert route.called
    assert list(candles["close"]) == [11.5, 12.5]


def test_missing_session_stops_before_any_request(tmp_path):
    market, _ = client_for(TokenStore(tmp_path / "none.json"))
    with pytest.raises(AuthExpiredError):
        market.option_chain("X", date(2026, 11, 17))


@respx.mock
def test_401_requires_login(tokens):
    respx.get(f"{BASE_URL}/v2/option/chain").respond(401, json={"status": "error"})
    market, _ = client_for(tokens)
    with pytest.raises(AuthExpiredError):
        market.option_chain("X", date(2026, 11, 17))


@respx.mock
def test_429_retries_with_backoff(tokens):
    route = respx.get(f"{BASE_URL}/v2/option/chain").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=CHAIN_RESPONSE)]
    )
    market, sleeps = client_for(tokens)
    assert not market.option_chain("X", date(2026, 11, 17)).empty
    assert route.call_count == 2
    assert sleeps == [2]


@respx.mock
def test_persistent_server_error_raises_after_limit(tokens):
    route = respx.get(f"{BASE_URL}/v2/option/chain").respond(503)
    market, _ = client_for(tokens)
    with pytest.raises(MarketDataError):
        market.option_chain("X", date(2026, 11, 17))
    assert route.call_count == 4


@respx.mock
def test_error_status_in_body_raises(tokens):
    respx.get(f"{BASE_URL}/v2/option/chain").respond(200, json={"status": "error"})
    market, _ = client_for(tokens)
    with pytest.raises(MarketDataError):
        market.option_chain("X", date(2026, 11, 17))


def test_rate_limiter_waits_for_the_tightest_window():
    state = {"now": 0.0, "slept": []}

    def sleep(seconds):
        state["slept"].append(seconds)
        state["now"] += seconds

    limiter = RateLimiter(((2, 1.0), (3, 60.0)), clock=lambda: state["now"], sleep=sleep)
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()
    assert state["slept"] == [1.0]
    limiter.acquire()
    assert state["now"] == 60.0


def store_chain(ts, expiry="2026-11-17"):
    return pd.DataFrame(
        {
            "ts": [ts],
            "expiry": [date.fromisoformat(expiry)],
            "strike": [24000.0],
            "option_type": ["CE"],
            "bid": [1.0],
        }
    )


def test_store_reads_only_requested_symbol_and_dates(tmp_path):
    store = SnapshotStore(tmp_path)
    store.write_chain("NIFTY", NOW, store_chain(NOW), "2026-11-17")
    store.write_chain("BANKNIFTY", NOW, store_chain(NOW), "2026-11-17")
    later = NOW + timedelta(days=2)
    store.write_chain("NIFTY", later, store_chain(later), "2026-11-17")
    frame = store.read_chains("NIFTY", NOW.date(), NOW.date())
    assert len(frame) == 1
    assert frame["bid"].iloc[0] == 1.0


def test_store_read_is_empty_before_collection(tmp_path):
    assert SnapshotStore(tmp_path).read_chains("NIFTY", NOW.date(), NOW.date()).empty


def test_contracts_are_point_in_time(tmp_path):
    store = SnapshotStore(tmp_path)
    store.write_contracts(date(2026, 1, 1), pd.DataFrame({"lot_size": [75]}))
    store.write_contracts(date(2026, 2, 1), pd.DataFrame({"lot_size": [65]}))
    assert store.read_contracts(date(2026, 1, 20))["lot_size"].iloc[0] == 75
    assert store.read_contracts(date(2026, 3, 1))["lot_size"].iloc[0] == 65
    assert store.read_contracts(date(2025, 12, 1)) is None


def test_candle_upsert_replaces_duplicates(tmp_path):
    store = SnapshotStore(tmp_path)
    store.upsert_candles("X", pd.DataFrame({"ts": [1, 2], "close": [10.0, 20.0]}))
    store.upsert_candles("X", pd.DataFrame({"ts": [2, 3], "close": [21.0, 30.0]}))
    assert list(store.read_candles("X")["close"]) == [10.0, 21.0, 30.0]


class FakeMarket:
    def __init__(self, failing=(), error=None):
        self.failing = set(failing)
        self.error = error or MarketDataError("boom")

    def option_chain(self, key, expiry):
        if expiry in self.failing:
            raise self.error
        return store_chain(NOW, expiry.isoformat())

    def daily_candles(self, key, start, end):
        if self.failing:
            raise self.error
        return pd.DataFrame({"ts": [1], "close": [1.0]})


UNIVERSE = Universe(
    underlyings=["NIFTY"], reference_indices={"VIX": "V"}, expiries_per_underlying=2
)


def test_collector_isolates_failures(tmp_path):
    result = collect_snapshots(
        FakeMarket({date(2026, 11, 17)}), SnapshotStore(tmp_path), UNIVERSE, contracts(), NOW
    )
    assert result.saved == 1
    assert list(result.failures) == ["NIFTY:2026-11-17"]


def test_collector_reports_unknown_symbol(tmp_path):
    universe = Universe(underlyings=["NIFTY", "NOPE"], expiries_per_underlying=1)
    result = collect_snapshots(FakeMarket(), SnapshotStore(tmp_path), universe, contracts(), NOW)
    assert result.saved == 1
    assert "NOPE" in result.failures


def test_collector_stops_on_expired_session(tmp_path):
    market = FakeMarket({date(2026, 11, 17)}, AuthExpiredError("login"))
    with pytest.raises(AuthExpiredError):
        collect_snapshots(market, SnapshotStore(tmp_path), UNIVERSE, contracts(), NOW)


def test_backfill_includes_reference_indices(tmp_path):
    store = SnapshotStore(tmp_path)
    assert backfill_daily(FakeMarket(), store, UNIVERSE, contracts(), NOW.date()).saved == 2
    assert not store.read_candles("VIX").empty


def test_backend_never_references_order_endpoints():
    forbidden = re.compile(
        r"/order/|place_order|/v\d/order|api-hft|modify_order|cancel_order", re.I
    )
    offenders = [str(path) for path in BACKEND.rglob("*.py") if forbidden.search(path.read_text())]
    assert offenders == []

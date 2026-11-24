from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from algoverse.api.app import create_app
from algoverse.brokers.auth import StoredToken, TokenStore
from algoverse.brokers.base import AuthExpiredError, MarketDataError
from algoverse.brokers.upstox import BASE_URL, UpstoxMarketData
from algoverse.market.institutions import (
    CASH,
    FII_TYPES,
    INDEX_FUTURES,
    INDEX_OPTIONS,
    build_institutions,
)
from algoverse.market.overview import OverviewService
from algoverse.settings import Settings
from conftest import CONFIG_DIR
from fastapi.testclient import TestClient

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 10, 16, 0, tzinfo=IST)


def stamp(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=IST).timestamp() * 1000)


def record(day, buy, sell, **fields):
    base = dict.fromkeys(
        [
            "buy_contracts",
            "sell_contracts",
            "oi_contracts",
            "oi_amount",
            "total_long_contracts",
            "total_short_contracts",
            "total_call_long_contracts",
            "total_put_long_contracts",
            "total_call_short_contracts",
            "total_put_short_contracts",
        ],
        0,
    )
    return {**base, "time_stamp": stamp(day), "buy_amount": buy, "sell_amount": sell, **fields}


D1, D2, D3 = date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 9)
FII = {
    CASH: [record(D3, 20311.0, 13099.0), record(D1, 100.0, 300.0), record(D2, 50.0, 40.0)],
    INDEX_FUTURES: [
        record(
            D3,
            4492.0,
            2677.0,
            oi_contracts=195505,
            total_long_contracts=52382,
            total_short_contracts=143123,
        ),
        record(D2, 1.0, 1.0, oi_contracts=190000),
    ],
    INDEX_OPTIONS: [
        record(
            D3,
            50.0,
            40.0,
            oi_contracts=100,
            total_call_long_contracts=600,
            total_call_short_contracts=700,
            total_put_long_contracts=900,
            total_put_short_contracts=500,
        )
    ],
}
DII = {CASH: [record(D3, 12765.0, 19938.0), record(D2, 80.0, 60.0)]}


def build():
    return build_institutions(FII, DII, {CASH: [record(D3, 10.0, 4.0)]}, {CASH: []}, NOW)


def test_cash_net_and_totals():
    data = build()
    assert data["as_of"] == D3
    assert data["fii_net"] == pytest.approx(7212.0)
    assert data["dii_net"] == pytest.approx(-7173.0)
    assert data["combined_net"] == pytest.approx(39.0)
    assert data["fii_recent"] == pytest.approx(7212.0 - 200.0 + 10.0)


def test_midnight_ist_stamps_stay_on_their_own_date():
    assert [row["date"] for row in build()["daily"]] == [D1, D2, D3]


def test_a_date_missing_on_one_side_is_null_not_zero():
    first = build()["daily"][0]
    assert first["date"] == D1 and first["fii_net"] == -200.0 and first["dii_net"] is None


def test_segments_are_all_shown_in_order():
    labels = [row["label"] for row in build()["segments"]]
    assert labels == ["FII cash", "DII cash", "FII index futures", "FII index options"]


def test_futures_positioning_and_oi_change():
    row = next(r for r in build()["segments"] if r["label"] == "FII index futures")
    assert row["net"] == pytest.approx(1815.0)
    assert row["oi_change"] == 5505
    assert row["long_pct"] == pytest.approx(52382 / 195505 * 100)
    assert row["call_net"] is None


def test_options_positioning():
    row = next(r for r in build()["segments"] if r["label"] == "FII index options")
    assert row["call_net"] == -100 and row["put_net"] == 400
    assert row["long_pct"] is None and row["oi_change"] is None


def test_cash_has_no_open_interest():
    row = build()["segments"][0]
    assert row["oi_contracts"] is None and row["oi_change"] is None and row["long_pct"] is None


class FlowMarket:
    def __init__(self):
        self.calls = 0
        self.error = None

    def institutional_flows(self, category, data_types, interval):
        self.calls += 1
        if self.error:
            raise self.error
        return FII if category == "fii" else DII


def service(tmp_path, market, clock):
    return OverviewService(tmp_path, CONFIG_DIR, market, None, None, clock=clock)


def test_flows_are_cached_between_polls(tmp_path):
    market = FlowMarket()
    now = [0.0]
    built = service(tmp_path, market, lambda: now[0])
    assert built.institutions(NOW)["status"] == "ok"
    assert built.institutions(NOW)["as_of"] == "2026-11-09"
    assert market.calls == 4
    now[0] = 1801.0
    built.institutions(NOW)
    assert market.calls == 8


@pytest.mark.parametrize(
    ("error", "status"),
    [(AuthExpiredError("x"), "session_expired"), (MarketDataError("x"), "unavailable")],
)
def test_failures_show_the_last_saved_data(tmp_path, error, status):
    market = FlowMarket()
    service(tmp_path, market, lambda: 0.0).institutions(NOW)
    market.error = error
    data = service(tmp_path, market, lambda: 0.0).institutions(NOW)
    assert data["status"] == status and data["fii_net"] == pytest.approx(7212.0)


def test_failure_with_nothing_saved_is_empty(tmp_path):
    market = FlowMarket()
    market.error = MarketDataError("x")
    data = service(tmp_path, market, lambda: 0.0).institutions(NOW)
    assert data["status"] == "unavailable" and data["daily"] == []


def test_endpoint_works_without_a_login(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, ai_enabled=False)
    response = TestClient(create_app(settings)).get("/api/overview/institutions")
    assert response.status_code == 200
    assert response.json()["status"] == "session_expired" and response.json()["daily"] == []


@respx.mock
def test_client_requests_every_segment(tmp_path):
    store = TokenStore(tmp_path / "token.json")
    store.save(StoredToken("secret-token", datetime(2099, 1, 1, tzinfo=IST)))
    route = respx.get(f"{BASE_URL}/v2/market/fii").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": {CASH: []}})
    )
    market = UpstoxMarketData(store, client=httpx.Client(base_url=BASE_URL), sleep=lambda _: None)
    assert market.institutional_flows("fii", FII_TYPES, "1D") == {CASH: []}
    query = route.calls[0].request.url.params
    assert query.get_list("data_type") == FII_TYPES and query["interval"] == "1D"

from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest
from algoverse.api.app import create_app
from algoverse.brokers.base import AuthExpiredError
from algoverse.market.index_details import (
    build_details,
    load_constituents,
    save_constituents,
)
from algoverse.market.overview import OverviewService
from algoverse.settings import Settings
from fastapi.testclient import TestClient

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 11, 11, 0, tzinfo=IST)
CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
HEADER = {"X-AlgoVerse-Client": "dashboard"}
ITEM = {"name": "Nifty 50", "label": "Nifty 50"}
ROW = {
    "last": 110.0,
    "change": 10.0,
    "change_pct": 10.0,
    "open": 101.0,
    "high": 112.0,
    "low": 99.0,
    "range_position": 80.0,
    "vs_nifty": 0.0,
    "returns": {"1W": 5.0},
    "quote_day": date(2026, 11, 10),
}
STOCKS = [
    {"isin": f"INE00000000{i}", "symbol": f"S{i}", "name": f"Stock {i}", "industry": "IT"}
    for i in range(3)
]


def bars(count=60, start=100.0):
    closes = [start + i for i in range(count)]
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(count)]
    return pd.DataFrame(
        {
            "date": days,
            "open": closes,
            "high": [c + 2 for c in closes],
            "low": [c - 2 for c in closes],
            "close": closes,
        }
    )


def quote(last, change):
    return {"last_price": last, "net_change": change}


def test_five_days_are_the_newest_with_daily_change():
    frame = bars()
    days = build_details(ITEM, ROW, frame, [], None, False)["five_days"]
    assert [day["date"] for day in days] == list(frame["date"].tail(5))
    assert days[-1]["close"] == 159.0
    assert days[-1]["change_pct"] == pytest.approx(1 / 158 * 100)


def test_details_carry_todays_figures():
    data = build_details(ITEM, ROW, bars(), [], None, False)
    assert data["previous_close"] == 100.0 and data["open"] == 101.0
    assert data["returns"] == {"1W": 5.0} and data["vs_nifty"] == 0.0


def test_levels_use_the_last_year_only():
    frame = bars(300)
    stats = build_details(ITEM, {**ROW, "last": 300.0}, frame, [], None, False)["stats"]
    assert stats["high_52w"] == frame["high"].max()
    assert stats["low_52w"] == frame["low"].tail(252).min()
    assert stats["from_high_pct"] == pytest.approx((300 / frame["high"].max() - 1) * 100)


def test_realised_volatility_matches_the_formula():
    frame = bars()
    closes = frame["close"].astype(float)
    expected = np.log(closes / closes.shift()).dropna().tail(20).std() * np.sqrt(252) * 100
    stats = build_details(ITEM, ROW, frame, [], None, False)["stats"]
    assert stats["realised_vol"] == pytest.approx(expected)


def test_volatility_of_india_vix_is_not_shown_and_it_has_no_members():
    data = build_details(ITEM, ROW, bars(), [], None, True)
    assert data["stats"]["realised_vol"] is None and data["has_constituents"] is False


def test_short_history_leaves_unknowable_figures_empty():
    stats = build_details(ITEM, ROW, bars(5), [], None, False)["stats"]
    assert stats["realised_vol"] is None and stats["average_range_pct"] is None


def test_no_data_at_all_does_not_fail():
    empty = pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    data = build_details(ITEM, None, empty, [], None, False)
    assert data["last"] is None and data["five_days"] == [] and data["stats"]["high_52w"] is None


def test_members_are_ranked_and_counted():
    quotes = {
        "NSE_EQ|INE000000000": quote(102.0, 2.0),
        "NSE_EQ|INE000000001": quote(95.0, -5.0),
        "NSE_EQ|INE000000002": quote(100.0, 0.0),
    }
    members = build_details(ITEM, ROW, bars(), STOCKS, quotes, False)["constituents"]
    assert [m["symbol"] for m in members["members"]] == ["S0", "S2", "S1"]
    assert (members["advancing"], members["declining"], members["unchanged"]) == (1, 1, 1)
    assert members["average_change"] == pytest.approx(-1.0)
    assert members["count"] == 3 and members["priced"] == 3


def test_a_stock_with_no_quote_is_counted_but_not_ranked():
    quotes = {"NSE_EQ|INE000000000": quote(102.0, 2.0)}
    members = build_details(ITEM, ROW, bars(), STOCKS, quotes, False)["constituents"]
    assert members["count"] == 3 and members["priced"] == 1


def test_no_list_means_no_members():
    assert build_details(ITEM, ROW, bars(), [], None, False)["constituents"] is None


def test_constituents_are_saved_per_index(tmp_path):
    save_constituents(tmp_path, "Nifty 50", STOCKS)
    assert load_constituents(tmp_path, "Nifty 50") == STOCKS
    assert load_constituents(tmp_path, "Nifty Bank") == []


class Market:
    def __init__(self):
        self.member_calls = 0
        self.error = None

    def quotes(self, keys):
        if keys and keys[0].startswith("NSE_EQ"):
            self.member_calls += 1
            if self.error:
                raise self.error
            return {key: quote(102.0, 2.0) for key in keys}
        return {
            key: {
                "last_price": 25000.0,
                "net_change": 100.0,
                "ohlc": {"open": 24900.0, "high": 25100.0, "low": 24800.0},
                "last_trade_time": str(int(NOW.timestamp() * 1000)),
            }
            for key in keys
        }

    def daily_candles(self, key, start, end):
        return pd.DataFrame()


class Store:
    def read_candles(self, name):
        frame = bars()
        ts = pd.to_datetime(frame["date"]).dt.tz_localize("Asia/Kolkata")
        return frame.drop(columns="date").assign(ts=ts, volume=0, oi=0)

    def upsert_candles(self, name, frame):
        pass


def service(tmp_path, market, clock=lambda: 0.0):
    calendar = SimpleNamespace(is_open=lambda now: True)
    return OverviewService(tmp_path, CONFIG_DIR, market, Store(), calendar, clock=clock)


def test_service_returns_details_and_refuses_unknown_names(tmp_path):
    built = service(tmp_path, Market())
    data = built.details("Nifty 50", NOW)
    assert data["label"] == "Nifty 50" and len(data["five_days"]) == 5
    assert data["constituents"] is None and data["last"] == 25000.0
    assert built.details("Not An Index", NOW) is None


def test_member_prices_are_cached(tmp_path):
    save_constituents(tmp_path, "Nifty 50", STOCKS)
    market = Market()
    now = [0.0]
    built = service(tmp_path, market, lambda: now[0])
    assert built.details("Nifty 50", NOW)["constituents"]["priced"] == 3
    built.details("Nifty 50", NOW)
    assert market.member_calls == 1
    now[0] = 25.0
    built.details("Nifty 50", NOW)
    assert market.member_calls == 2


def test_failed_member_prices_still_show_the_card(tmp_path):
    save_constituents(tmp_path, "Nifty 50", STOCKS)
    market = Market()
    market.error = AuthExpiredError("x")
    data = service(tmp_path, market).details("Nifty 50", NOW)
    assert data["constituents"]["count"] == 3 and data["constituents"]["priced"] == 0


def test_vix_is_not_offered_a_list(tmp_path):
    built = service(tmp_path, Market())
    names = [item["name"] for item in built.constituent_lists()]
    assert "Nifty 50" in names and "India VIX" not in names
    assert len(names) == len(set(names))
    assert built.accepts_constituents("Nifty 50") and not built.accepts_constituents("India VIX")


CSV = (
    b"Company Name,Industry,Symbol,Series,ISIN Code\n"
    b"Reliance Industries Ltd.,Oil Gas,RELIANCE,EQ,INE002A01018\n"
)


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, ai_enabled=False)
    return TestClient(create_app(settings))


def test_upload_saves_the_list(client):
    response = client.post(
        "/api/overview/constituents", params={"name": "Nifty 50"}, content=CSV, headers=HEADER
    )
    assert response.json() == {"stocks": 1}
    lists = client.get("/api/overview/constituents").json()["indices"]
    assert next(i for i in lists if i["name"] == "Nifty 50")["stocks"] == 1


def test_upload_needs_the_dashboard_header(client):
    response = client.post(
        "/api/overview/constituents", params={"name": "Nifty 50"}, content=CSV
    )
    assert response.status_code == 403


def test_upload_refuses_unknown_index_and_bad_file(client):
    bad_name = client.post(
        "/api/overview/constituents", params={"name": "../evil"}, content=CSV, headers=HEADER
    )
    assert bad_name.status_code == 404
    bad_file = client.post(
        "/api/overview/constituents", params={"name": "Nifty 50"}, content=b"a,b\n", headers=HEADER
    )
    assert bad_file.status_code == 400


def test_details_endpoint_refuses_unknown_names(client):
    assert client.get("/api/overview/details", params={"name": "Nope"}).status_code == 404

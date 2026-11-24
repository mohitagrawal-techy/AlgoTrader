from datetime import date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest
import respx
from algoverse.api.app import create_app
from algoverse.brokers.auth import StoredToken, TokenStore
from algoverse.brokers.base import AuthExpiredError, MarketDataError
from algoverse.brokers.upstox import BASE_URL, UpstoxMarketData
from algoverse.market.overview import (
    OverviewService,
    breadth,
    build_index_row,
    candle_rows,
    distribution,
    heatmap,
    index_key,
    industries,
    merge_live_bar,
    movers,
    period_returns,
    stock_frame,
)
from algoverse.market.sectors import (
    SectorListError,
    load_sector_list,
    parse_sector_csv,
    save_sector_list,
)
from algoverse.settings import Settings
from conftest import CONFIG_DIR, IST, NOW
from fastapi.testclient import TestClient

CSV = (
    b"Company Name,Industry,Symbol,Series,ISIN Code\n"
    b"Alpha Ltd,Information Technology,ALPHA,EQ,INE000000001\n"
    b"Beta Ltd,Information Technology,BETA,EQ,INE000000002\n"
    b"Gamma Ltd,Metals,GAMMA,EQ,INE000000003\n"
    b"Bad Row,Metals,BAD,EQ,not-an-isin\n"
)
STOCKS = parse_sector_csv(CSV)
TRADE_MS = str(int(datetime(2026, 11, 12, 15, 29, tzinfo=IST).timestamp() * 1000))


def quote(last, change, volume=None, average=None, high=None, low=None):
    return {
        "last_price": last,
        "net_change": change,
        "volume": volume,
        "average_price": average,
        "ohlc": {"open": last, "high": high or last, "low": low or last, "close": last},
        "last_trade_time": TRADE_MS,
    }


def eq(i):
    return f"NSE_EQ|INE00000000{i}"


QUOTES = {
    eq(1): quote(110.0, 10.0, 1000, 105.0),
    eq(2): quote(95.0, -5.0, 2000, 96.0),
    eq(3): quote(50.0, 0.0, 400, 50.0),
}


def test_sector_csv_keeps_valid_rows_only():
    assert [s["symbol"] for s in STOCKS] == ["ALPHA", "BETA", "GAMMA"]
    assert STOCKS[0]["industry"] == "Information Technology"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"Name,Sector\nA,B\n", "Missing columns"),
        (b"Company Name,Industry,Symbol,ISIN Code\nA,B,C,bad\n", "No valid rows"),
        (b"\xff\xfe\x00", "UTF-8"),
        (b"a" * 2_000_001, "2 MB"),
    ],
)
def test_bad_sector_files_are_refused_with_a_reason(content, message):
    with pytest.raises(SectorListError, match=message):
        parse_sector_csv(content)


def test_stock_change_uses_previous_close():
    frame = stock_frame(STOCKS, QUOTES)
    assert frame.set_index("symbol").loc["ALPHA", "change_pct"] == pytest.approx(10.0)
    assert frame.set_index("symbol").loc["BETA", "change_pct"] == pytest.approx(-5.0)
    assert frame.set_index("symbol").loc["ALPHA", "value"] == pytest.approx(105000.0)


def test_untraded_or_missing_stocks_are_left_out():
    quotes = {eq(1): quote(10.0, 1.0, 0, 10.0)}
    assert stock_frame(STOCKS, quotes).empty


def test_breadth_hand_calculation():
    result = breadth(stock_frame(STOCKS, QUOTES))
    assert (result["advancing"], result["declining"], result["unchanged"]) == (1, 1, 1)
    assert result["average_change"] == pytest.approx(5 / 3)
    assert (result["up_over_2"], result["down_over_2"]) == (1, 1)
    assert result["value"] == pytest.approx(105000 + 192000 + 20000)


def test_industries_are_sorted_with_best_and_worst():
    rows = industries(stock_frame(STOCKS, QUOTES))
    assert [r["industry"] for r in rows] == ["Information Technology", "Metals"]
    assert rows[0]["average_change"] == pytest.approx(2.5)
    assert rows[0]["best"]["symbol"] == "ALPHA" and rows[0]["worst"]["symbol"] == "BETA"
    assert (rows[0]["advancing"], rows[0]["declining"]) == (1, 1)


def test_movers_and_heatmap_order():
    frame = stock_frame(STOCKS, QUOTES)
    picks = movers(frame)
    assert picks["gainers"][0]["symbol"] == "ALPHA"
    assert picks["losers"][0]["symbol"] == "BETA"
    assert picks["active"][0]["symbol"] == "BETA"
    groups = heatmap(frame)
    assert groups[0]["industry"] == "Information Technology"
    assert [s["symbol"] for s in groups[0]["stocks"]] == ["BETA", "ALPHA"]


def closes(values, end=date(2026, 11, 11)):
    days = pd.bdate_range(end=pd.Timestamp(end), periods=len(values), tz="Asia/Kolkata")
    return pd.DataFrame({"ts": days, "close": values})


def test_period_returns_count_completed_sessions_before_the_quote():
    frame = closes([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    # On 12 Nov, one week back is the fifth-last completed close: 101.
    result = period_returns(frame, 111.1, date(2026, 11, 12))
    assert result == {"1W": pytest.approx(10.0)}


def test_period_returns_exclude_the_quote_session_itself():
    frame = closes([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    # A closed-market quote dated 11 Nov must not count 11 Nov's own bar.
    assert period_returns(frame, 110.0, date(2026, 11, 11)) == {"1W": pytest.approx(10.0)}


def test_period_returns_skip_short_history():
    assert period_returns(closes([100.0]), 110.0, date(2026, 11, 12)) == {}


def test_index_row_hand_calculation():
    item = {"name": "Nifty IT", "label": "IT"}
    row = build_index_row(item, quote(101.0, 1.0, high=102.0, low=98.0), pd.DataFrame(), 0.25)
    assert row["change_pct"] == pytest.approx(1.0)
    assert row["vs_nifty"] == pytest.approx(0.75)
    assert row["range_position"] == pytest.approx(75.0)


def test_index_row_needs_a_quote():
    assert build_index_row({"name": "x", "label": "x"}, None, pd.DataFrame(), None) is None


@respx.mock
def test_quotes_batch_at_500_and_key_by_request_format(tmp_path):
    tokens = TokenStore(tmp_path / "token.json")
    tokens.save(StoredToken("t", datetime.now(IST) + timedelta(hours=1)))
    keys = [f"NSE_EQ|K{i}" for i in range(501)]

    def reply(request):
        requested = request.url.params["instrument_key"].split(",")
        data = {f"NSE_EQ:S{k}": {"instrument_token": k, "last_price": 1.0} for k in requested}
        return httpx.Response(200, json={"status": "success", "data": data})

    route = respx.get(f"{BASE_URL}/v2/market-quote/quotes").mock(side_effect=reply)
    client = httpx.Client(base_url=BASE_URL)
    result = UpstoxMarketData(tokens, client=client).quotes(keys)
    assert route.call_count == 2
    assert set(result) == set(keys)


class FakeMarket:
    def __init__(self):
        self.calls = 0
        self.error = None

    def quotes(self, keys):
        self.calls += 1
        if self.error:
            raise self.error
        indices = {
            k: quote(25000.0, 100.0, high=25100.0, low=24800.0) for k in keys if "INDEX" in k
        }
        return indices | {k: QUOTES[k] for k in keys if k in QUOTES}

    def daily_candles(self, key, start, end):
        return pd.DataFrame()


class FakeStore:
    def read_candles(self, name):
        return pd.DataFrame()

    def upsert_candles(self, name, frame):
        pass


def service(tmp_path, is_open=True):
    state = SimpleNamespace(now=0.0)
    market = FakeMarket()
    calendar = SimpleNamespace(is_open=lambda now: is_open)
    built = OverviewService(
        tmp_path, CONFIG_DIR, market, FakeStore(), calendar, clock=lambda: state.now
    )
    return built, market, state


def test_snapshot_without_sector_list_still_shows_sectors(tmp_path):
    built, _, _ = service(tmp_path)
    data = built.snapshot(NOW)
    assert data["status"] == "live" and not data["has_sector_list"]
    assert len(data["sectors"]) == 22 and len(data["indices"]) == 6
    assert data["volatility"]["label"] == "India VIX"
    assert data["breadth"] is None


def test_snapshot_with_sector_list_adds_stock_views(tmp_path):
    save_sector_list(tmp_path, STOCKS)
    built, _, _ = service(tmp_path)
    data = built.snapshot(NOW)
    assert data["has_sector_list"] and data["breadth"]["stocks"] == 3
    assert data["industries"] and data["heatmap"] and data["movers"]["gainers"]


def test_polling_is_cached_for_twenty_seconds_when_open(tmp_path):
    built, market, state = service(tmp_path)
    built.snapshot(NOW)
    state.now = 19.0
    built.snapshot(NOW)
    assert market.calls == 1
    state.now = 21.0
    built.snapshot(NOW)
    assert market.calls == 2


def test_closed_market_is_cached_longer(tmp_path):
    built, market, state = service(tmp_path, is_open=False)
    assert built.snapshot(NOW)["status"] == "closed"
    state.now = 200.0
    built.snapshot(NOW)
    assert market.calls == 1


@pytest.mark.parametrize(
    ("error", "status"),
    [(AuthExpiredError("x"), "session_expired"), (MarketDataError("x"), "unavailable")],
)
def test_failures_show_the_last_saved_data(tmp_path, error, status):
    built, market, state = service(tmp_path)
    good = built.snapshot(NOW)
    market.error = error
    state.now = 100.0
    data = built.snapshot(NOW)
    assert data["status"] == status
    assert data["sectors"] == good["sectors"]


def test_failure_with_nothing_saved_is_empty_not_an_error(tmp_path):
    built, market, _ = service(tmp_path)
    market.error = AuthExpiredError("x")
    data = built.snapshot(NOW)
    assert data["status"] == "session_expired" and data["indices"] == []


def test_index_keys_use_the_upstox_format():
    assert index_key("Nifty 50") == "NSE_INDEX|Nifty 50"


def test_upload_api_requires_dashboard_header_and_saves(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, ai_enabled=False)
    client = TestClient(create_app(settings))
    assert client.post("/api/overview/sectors", content=CSV).status_code == 403
    headers = {"X-AlgoVerse-Client": "dashboard"}
    bad = client.post("/api/overview/sectors", content=b"x,y\n1,2\n", headers=headers)
    assert bad.status_code == 400 and "Missing columns" in bad.json()["detail"]
    good = client.post("/api/overview/sectors", content=CSV, headers=headers)
    assert good.json() == {"stocks": 3}
    assert len(load_sector_list(tmp_path)) == 3


def test_overview_api_reports_login_needed(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, ai_enabled=False)
    body = TestClient(create_app(settings)).get("/api/overview").json()
    assert body["status"] == "session_expired" and body["options"] == []


def bars_frame(count=60):
    days = [d.date() for d in pd.bdate_range(end="2026-11-11", periods=count)]
    closes = [100.0 + i for i in range(count)]
    return pd.DataFrame(
        {
            "date": days,
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
        }
    )


LIVE = {"quote_day": date(2026, 11, 12), "open": 1.0, "high": 3.0, "low": 0.5, "last": 2.0}


def test_distribution_bands_are_counted():
    frame = pd.DataFrame({"change_pct": [-6.0, -1.5, -0.5, 0.0, 0.5, 2.5, 7.0], "value": 1.0})
    counts = {row["label"]: row["count"] for row in distribution(frame)}
    assert counts["Below -5%"] == 1 and counts["-2 to -1%"] == 1 and counts["-1 to 0%"] == 1
    assert counts["0 to 1%"] == 2 and counts["2 to 3%"] == 1 and counts["Above 5%"] == 1
    assert sum(counts.values()) == 7


def test_live_quote_is_appended_as_the_newest_bar():
    merged = merge_live_bar(bars_frame(), LIVE)
    assert len(merged) == 61 and merged.iloc[-1]["close"] == 2.0


def test_live_quote_replaces_a_bar_for_the_same_day():
    live = LIVE | {"quote_day": "2026-11-11"}
    merged = merge_live_bar(bars_frame(), live)
    assert len(merged) == 60 and merged.iloc[-1]["close"] == 2.0


def test_incomplete_live_quote_is_ignored():
    frame = bars_frame()
    assert merge_live_bar(frame, LIVE | {"high": None}) is frame
    assert merge_live_bar(frame, None) is frame


def test_candles_have_moving_averages_from_the_full_history():
    rows = candle_rows(bars_frame(60), sessions=10)
    assert len(rows) == 10
    assert rows[-1]["ema20"] is not None and rows[-1]["ema50"] is not None
    assert rows[-1]["ema20"] > rows[-1]["ema50"]


class HistoryStore(FakeStore):
    def read_candles(self, name):
        frame = bars_frame(30)
        ts = pd.to_datetime(frame["date"]).dt.tz_localize("Asia/Kolkata")
        return frame.drop(columns="date").assign(ts=ts)


def history_service(tmp_path):
    calendar = SimpleNamespace(is_open=lambda now: True)
    return OverviewService(tmp_path, CONFIG_DIR, FakeMarket(), HistoryStore(), calendar)


def test_history_includes_live_candle_and_refuses_unknown_names(tmp_path):
    built = history_service(tmp_path)
    assert built.history("Not An Index", NOW) is None
    result = built.history("Nifty 50", NOW)
    assert result["label"] == "Nifty 50"
    assert result["candles"][-1]["date"] == "2026-11-12"
    assert result["candles"][-1]["close"] == 25000.0


def test_comparison_aligns_dates_and_leaves_out_nifty_500(tmp_path):
    result = history_service(tmp_path).comparison(NOW)
    labels = [item["label"] for item in result["series"]]
    assert labels == ["Nifty 50", "Next 50", "Bank Nifty", "Midcap 100", "Smallcap 100"]
    assert all(len(item["closes"]) == len(result["dates"]) for item in result["series"])
    assert result["volatility"]["closes"]


class TwiceStoredStore(HistoryStore):
    """A backfill stored one session twice, at two different times."""

    def read_candles(self, name):
        frame = super().read_candles(name)
        again = frame.iloc[[5]].assign(ts=frame["ts"].iloc[5] + pd.Timedelta(hours=15))
        return pd.concat([frame, again], ignore_index=True)


def test_a_session_stored_twice_is_charted_once(tmp_path):
    calendar = SimpleNamespace(is_open=lambda now: True)
    built = OverviewService(tmp_path, CONFIG_DIR, FakeMarket(), TwiceStoredStore(), calendar)
    result = built.comparison(NOW)
    assert len(result["dates"]) == len(set(result["dates"]))
    dates = [row["date"] for row in built.history("Nifty 50", NOW)["candles"]]
    assert len(dates) == len(set(dates))

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import pytest
import respx
from algoverse.backtest.bhavcopy import COLUMNS, history_dir, store_day
from algoverse.backtest.dataview import History
from algoverse.backtest.upstox_history import (
    CaptureRefused,
    build_capture,
    capture_session,
    import_expired,
)
from algoverse.brokers.auth import StoredToken, TokenStore
from algoverse.brokers.base import AuthExpiredError, MarketDataError, PlanRequiredError
from algoverse.brokers.upstox import BASE_URL, UpstoxMarketData
from algoverse.data.instruments import parse_future_contracts

IST = ZoneInfo("Asia/Kolkata")
INDEX_KEY = "NSE_INDEX|Nifty 50"
SESSION = date(2026, 11, 12)
CONFIG = SimpleNamespace(symbols=["NIFTY"], strike_range=0.15, expiries_per_underlying=2)


def epoch(day):
    return str(int(datetime(day.year, day.month, day.day, 15, 29, tzinfo=IST).timestamp() * 1000))


def contract_table(expiries=(date(2026, 11, 17), date(2026, 11, 24)), strikes=(23500.0, 24000.0)):
    rows = [
        {
            "instrument_key": f"O|{expiry}|{strike:g}|{kind}",
            "underlying_symbol": "NIFTY",
            "underlying_key": INDEX_KEY,
            "expiry": expiry,
            "strike": strike,
            "option_type": kind,
            "lot_size": 65,
        }
        for expiry in expiries
        for strike in strikes
        for kind in ("CE", "PE")
    ]
    return pd.DataFrame(rows)


def futures_table():
    return pd.DataFrame(
        [
            {
                "instrument_key": "F|near",
                "underlying_symbol": "NIFTY",
                "expiry": date(2026, 11, 24),
                "lot_size": 65,
            }
        ]
    )


class QuoteMarket:
    def __init__(self, traded=SESSION, missing=()):
        self.traded = traded
        self.missing = set(missing)

    def quotes(self, keys):
        return {
            key: {
                "last_price": 24000.0 if key == INDEX_KEY else 100.0,
                "last_trade_time": epoch(self.traded),
                "ohlc": {"open": 90.0, "high": 110.0, "low": 85.0, "close": 100.0},
                "volume": 6500,
                "oi": 13000.0,
            }
            for key in keys
            if key not in self.missing
        }


def test_capture_builds_a_session_the_engine_can_read(tmp_path):
    capture_session(QuoteMarket(), tmp_path, CONFIG, contract_table(), futures_table(), SESSION)
    history = History(tmp_path)
    assert history.sessions == [SESSION]
    day = history.day(SESSION)
    assert list(day.columns) == COLUMNS
    assert (day["instrument"] == "OPT").sum() == 8
    assert history.bars("NIFTY")["close"].iloc[0] == 100.0
    assert day["underlying_price"].iloc[0] == 24000.0


def test_capture_refuses_when_nothing_traded_today():
    market = QuoteMarket(traded=SESSION - timedelta(days=1))
    with pytest.raises(CaptureRefused, match="looks closed"):
        build_capture(market, CONFIG, contract_table(), futures_table(), SESSION)


def test_capture_refuses_a_chain_with_missing_quotes():
    table = contract_table()
    gone = table.loc[table["expiry"] == date(2026, 11, 17), "instrument_key"]
    market = QuoteMarket(missing=gone)
    with pytest.raises(CaptureRefused, match="2026-11-17 options"):
        build_capture(market, CONFIG, table, futures_table(), SESSION)


def test_capture_marks_an_untraded_contract_as_not_traded():
    table = contract_table()
    stale_key = table["instrument_key"].iloc[0]

    class Partly(QuoteMarket):
        def quotes(self, keys):
            rows = super().quotes(keys)
            if stale_key in rows:
                rows[stale_key]["last_trade_time"] = epoch(SESSION - timedelta(days=3))
            return rows

    frame = build_capture(Partly(), CONFIG, table, futures_table(), SESSION)
    row = frame[(frame["strike"] == 23500.0) & (frame["option_type"] == "CE")].iloc[0]
    assert row["volume"] == 0.0
    assert row["close"] == 100.0


def test_capture_reports_oi_change_against_the_previous_session():
    first = build_capture(QuoteMarket(), CONFIG, contract_table(), futures_table(), SESSION)
    first["oi"] = 10000.0
    frame = build_capture(
        QuoteMarket(), CONFIG, contract_table(), futures_table(), SESSION, previous=first
    )
    assert set(frame["oi_change"]) == {3000.0}


def test_capture_never_replaces_a_session_from_nse_files(tmp_path):
    original = build_capture(QuoteMarket(), CONFIG, contract_table(), futures_table(), SESSION)
    original["close"] = 7.0
    store_day(tmp_path, original)
    with pytest.raises(CaptureRefused, match="NSE files"):
        capture_session(QuoteMarket(), tmp_path, CONFIG, contract_table(), futures_table(), SESSION)
    assert set(History(tmp_path).day(SESSION)["close"]) == {7.0}


def test_capture_can_be_repeated_for_the_same_day(tmp_path):
    for _ in range(2):
        capture_session(QuoteMarket(), tmp_path, CONFIG, contract_table(), futures_table(), SESSION)
    assert History(tmp_path).sessions == [SESSION]


def test_future_contracts_keep_only_requested_underlyings():
    rows = [
        {
            "segment": "NSE_FO",
            "instrument_type": "FUT",
            "underlying_symbol": symbol,
            "instrument_key": f"NSE_FO|{symbol}",
            "expiry": 1795458599000,
            "lot_size": 65,
        }
        for symbol in ("NIFTY", "RELIANCE")
    ]
    frame = parse_future_contracts(rows, {"NIFTY"})
    assert list(frame["underlying_symbol"]) == ["NIFTY"]
    assert frame["expiry"].iloc[0] == date(2026, 11, 23)


# ---- Expired contracts -----------------------------------------------------------------------

TODAY = date(2026, 11, 12)
EXPIRED = [date(2026, 11, 3), date(2026, 11, 10)]
LIVE = (date(2026, 11, 17), date(2026, 11, 24))


def candles(start, end, close=100.0):
    days = [d.date() for d in pd.date_range(start, end, freq="B")]
    return pd.DataFrame(
        {
            "ts": [pd.Timestamp(d, tz="Asia/Kolkata") for d in days],
            "open": close - 1,
            "high": close + 5,
            "low": close - 5,
            "close": close,
            "volume": 6500.0,
            "oi": [13000.0 + 650 * i for i in range(len(days))],
        }
    )


class ExpiredMarket:
    def __init__(self, expiries=EXPIRED, plan=True, fail=()):
        self.expiries = list(expiries)
        self.plan = plan
        self.fail = set(fail)
        self.candle_calls = []

    def expired_expiries(self, key):
        if not self.plan:
            raise PlanRequiredError("needs plan")
        return self.expiries

    def expired_contracts(self, key, kind, expiry):
        common = {"lot_size": 75}
        if kind == "future":
            return [{"instrument_key": f"X|FUT|{expiry}", "instrument_type": "FUT", **common}]
        return [
            {
                "instrument_key": f"X|{strike}|{expiry}",
                "instrument_type": "CE",
                "strike_price": strike,
                **common,
            }
            for strike in (24000, 40000)
        ]

    def expired_daily_candles(self, key, start, end):
        self.candle_calls.append(key)
        if key in self.fail:
            raise MarketDataError("bad contract")
        return candles(max(start, end - timedelta(days=14)), end)

    def daily_candles(self, key, start, end):
        if key == INDEX_KEY:
            return candles(start, end, close=24000.0)
        return candles(end - timedelta(days=7), end)


def run_import(market, data_dir):
    return import_expired(
        market, data_dir, CONFIG, contract_table(LIVE), futures_table(), TODAY
    )


def test_import_without_the_plan_saves_nothing(tmp_path):
    with pytest.raises(PlanRequiredError):
        run_import(ExpiredMarket(plan=False), tmp_path)
    assert not history_dir(tmp_path).exists()


def test_import_writes_only_sessions_with_every_expiry(tmp_path):
    result = run_import(ExpiredMarket(), tmp_path)
    # Days before the first known expiry could be missing an even earlier one.
    assert result.written[0] == EXPIRED[0]
    assert result.written[-1] == TODAY - timedelta(days=1)
    history = History(tmp_path)
    day = history.day(date(2026, 11, 5))
    options = day[day["instrument"] == "OPT"]
    assert set(options["expiry"]) >= {EXPIRED[1], LIVE[0]}
    assert EXPIRED[0] not in set(options["expiry"])  # Already expired on this day.
    first = history.day(EXPIRED[0])
    assert EXPIRED[0] in set(first["expiry"])
    assert 40000.0 not in set(options["strike"])  # Outside the strike band.
    assert set(options.loc[options["expiry"] == EXPIRED[1], "lot_size"]) == {75}
    assert not history.bars("NIFTY").empty


def test_import_resumes_without_fetching_finished_expiries(tmp_path):
    run_import(ExpiredMarket(), tmp_path)
    again = ExpiredMarket()
    run_import(again, tmp_path)
    assert again.candle_calls == []


def test_import_retries_an_expiry_with_too_many_failures(tmp_path):
    market = ExpiredMarket(fail={f"X|24000|{EXPIRED[1]}"})
    result = run_import(market, tmp_path)
    assert f"NIFTY {EXPIRED[1]}" in result.failed
    assert result.written == []
    healed = run_import(ExpiredMarket(), tmp_path)
    assert healed.written and healed.failed == {}


def test_import_keeps_sessions_from_nse_files(tmp_path):
    day = date(2026, 11, 5)
    frame = pd.DataFrame([dict.fromkeys(COLUMNS, 1.0)])
    frame = frame.assign(
        date=day, symbol="NIFTY", instrument="OPT", expiry=day, option_type="CE", close=7.0
    )
    store_day(tmp_path, frame[COLUMNS])
    result = run_import(ExpiredMarket(), tmp_path)
    assert day in result.kept_existing and day not in result.written
    assert set(History(tmp_path).day(day)["close"]) == {7.0}


# ---- Upstox client ---------------------------------------------------------------------------


@pytest.fixture
def market(tmp_path):
    store = TokenStore(tmp_path / "token.json")
    store.save(StoredToken("secret-token", datetime.now(IST) + timedelta(hours=1)))
    return UpstoxMarketData(store, client=httpx.Client(base_url=BASE_URL), sleep=lambda _: None)


@respx.mock
def test_missing_plan_is_not_reported_as_an_expired_login(market):
    body = {"status": "error", "errors": [{"errorCode": "UDAPI1149", "message": "Plus only"}]}
    respx.get(f"{BASE_URL}/v2/expired-instruments/expiries").mock(
        return_value=httpx.Response(401, json=body)
    )
    with pytest.raises(PlanRequiredError):
        market.expired_expiries(INDEX_KEY)


@respx.mock
def test_other_401_still_means_login_again(market):
    respx.get(f"{BASE_URL}/v2/expired-instruments/expiries").mock(
        return_value=httpx.Response(401, json={"status": "error", "errors": []})
    )
    with pytest.raises(AuthExpiredError):
        market.expired_expiries(INDEX_KEY)


@respx.mock
def test_expired_candles_use_the_documented_path(market):
    route = respx.get(url__startswith=f"{BASE_URL}/v2/expired-instruments/historical-candle/").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "candles": [
                        ["2025-04-24T00:00:00+05:30", 2, 3, 1, 2, 75, 150],
                        ["2025-04-23T00:00:00+05:30", 1, 2, 1, 1, 75, 75],
                    ]
                },
            },
        )
    )
    frame = market.expired_daily_candles(
        "NSE_FO|73507|24-04-2025", date(2025, 4, 1), date(2025, 4, 24)
    )
    url = str(route.calls[0].request.url)
    assert "NSE_FO%7C73507%7C24-04-2025/day/2025-04-24/2025-04-01" in url
    assert list(frame["close"]) == [1, 2]

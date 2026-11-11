from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from algoverse.brokers.base import AuthExpiredError
from algoverse.data.calendar import MarketCalendar
from algoverse.market.assistant import AssistantService
from algoverse.market.overview import OverviewService

IST = ZoneInfo("Asia/Kolkata")
CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
TODAY = date(2026, 11, 11)  # A Wednesday.
HOLIDAY = date(2026, 11, 10)
CALENDAR = MarketCalendar({HOLIDAY})


def candles(day, count=6, start=25000.0):
    times = [
        datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST) + timedelta(minutes=5 * i)
        for i in range(count)
    ]
    closes = [start + 10 * i for i in range(count)]
    return pd.DataFrame(
        {
            "ts": times,
            "open": closes,
            "high": [c + 5 for c in closes],
            "low": [c - 5 for c in closes],
            "close": closes,
            "volume": 0,
            "oi": 0,
        }
    )


class Market:
    def __init__(self, today_count=6):
        self.today_count = today_count
        self.intraday_calls = []
        self.past_calls = []
        self.error = None

    def intraday_candles(self, key, minutes):
        if self.error:
            raise self.error
        self.intraday_calls.append(key)
        return candles(TODAY, self.today_count)

    def minute_candles(self, key, day, minutes):
        if self.error:
            raise self.error
        self.past_calls.append((key, day))
        return candles(day, 75, 24000.0)

    def quotes(self, keys):
        return {}

    def daily_candles(self, key, start, end):
        return pd.DataFrame()


class Store:
    """Daily closes up to and including the 9 Nov session, which closed at 24,900."""

    def read_candles(self, name):
        days = pd.bdate_range("2026-10-01", "2026-11-09")
        frame = pd.DataFrame({"ts": days.tz_localize(IST)})
        frame["open"] = frame["high"] = frame["low"] = 24800.0
        frame["close"] = 24800.0
        frame.loc[frame.index[-1], "close"] = 24900.0
        return frame

    def upsert_candles(self, name, frame):
        pass


def service(tmp_path, market, clock=lambda: 0.0):
    return OverviewService(tmp_path, CONFIG_DIR, market, Store(), CALENDAR, clock=clock)


def at(day, hour, minute=0, second=0):
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=IST)


def test_during_the_session_it_shows_todays_candles(tmp_path):
    market = Market()
    data = service(tmp_path, market).intraday("Nifty 50", at(TODAY, 10, 0))
    assert data["session"] == TODAY.isoformat() and len(data["candles"]) == 6
    assert data["candles"][0]["date"].startswith("2026-11-11T09:15:00+05:30")
    assert market.intraday_calls == ["NSE_INDEX|Nifty 50"] and market.past_calls == []


def test_previous_close_is_the_last_session_before_it(tmp_path):
    data = service(tmp_path, Market()).intraday("Nifty 50", at(TODAY, 10, 0))
    assert data["previous_close"] == 24900.0


def test_before_the_open_it_shows_the_last_session_skipping_the_holiday(tmp_path):
    market = Market()
    data = service(tmp_path, market).intraday("Nifty 50", at(TODAY, 8, 30))
    assert data["session"] == "2026-11-09"
    assert market.intraday_calls == [] and market.past_calls == [
        ("NSE_INDEX|Nifty 50", date(2026, 11, 9))
    ]
    assert len(data["candles"]) == 75


def test_before_the_first_candle_forms_it_falls_back_to_the_last_session(tmp_path):
    data = service(tmp_path, Market(today_count=0)).intraday("Nifty 50", at(TODAY, 9, 16))
    assert data["session"] == "2026-11-09" and len(data["candles"]) == 75


def test_on_a_weekend_it_shows_friday(tmp_path):
    data = service(tmp_path, Market()).intraday("Nifty 50", at(date(2026, 11, 14), 11))
    assert data["session"] == "2026-11-13"


def test_candles_are_cached_between_polls(tmp_path):
    market = Market()
    now = [0.0]
    built = service(tmp_path, market, lambda: now[0])
    built.intraday("Nifty 50", at(TODAY, 10))
    built.intraday("Nifty 50", at(TODAY, 10))
    assert len(market.intraday_calls) == 1
    now[0] = 31.0
    built.intraday("Nifty 50", at(TODAY, 10))
    assert len(market.intraday_calls) == 2


def test_unknown_names_are_refused(tmp_path):
    assert service(tmp_path, Market()).intraday("Not an index", at(TODAY, 10)) is None


def test_an_expired_login_is_reported_not_raised(tmp_path):
    market = Market()
    market.error = AuthExpiredError("x")
    data = service(tmp_path, market).intraday("Nifty 50", at(TODAY, 10))
    assert data["status"] == "session_expired" and data["candles"] == []


def test_comparison_covers_the_main_indices_and_vix(tmp_path):
    data = service(tmp_path, Market()).intraday_comparison(at(TODAY, 10))
    labels = [item["label"] for item in data["series"]]
    assert "Nifty 50" in labels and "Nifty 500" not in labels and "India VIX" not in labels
    assert data["volatility"]["label"] == "India VIX"
    assert data["session"] == TODAY.isoformat()
    first = data["series"][0]
    assert len(first["times"]) == len(first["closes"]) == 6 and first["previous_close"] == 24900.0


def test_assistant_explains_one_session(tmp_path):
    assistant = AssistantService.__new__(AssistantService)
    assistant.overview = service(tmp_path, Market())
    summary = assistant._intraday_price("Nifty 50", at(TODAY, 10))
    assert summary["session"] == TODAY.isoformat() and summary["latest"] == 25050.0
    assert summary["change_from_previous_close_pct"] == pytest.approx((25050 / 24900 - 1) * 100)
    moves = assistant._intraday_moves(at(TODAY, 10))
    assert moves["indices"][0]["change_from_previous_close_pct"] == pytest.approx(
        (25050 / 24900 - 1) * 100
    )


from algoverse.data.stream import write_status  # noqa: E402
from algoverse.data.ticks import write_table  # noqa: E402
from algoverse.market.overview import resample_candles, with_live_price  # noqa: E402


def one_minute(day, count, start=25000.0, hour=9, minute=15):
    first = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
    closes = [start + i for i in range(count)]
    return pd.DataFrame(
        {
            "ts": [first + timedelta(minutes=i) for i in range(count)],
            "open": [c - 0.5 for c in closes],
            "high": [c + 2 for c in closes],
            "low": [c - 2 for c in closes],
            "close": closes,
            "volume": 0,
            "oi": 0,
        }
    )


def test_one_minute_candles_combine_into_five():
    bars = resample_candles(one_minute(TODAY, 12), 5)
    assert len(bars) == 3
    first = bars.iloc[0]
    assert first["open"] == 24999.5 and first["close"] == 25004.0
    assert first["high"] == 25006.0 and first["low"] == 24998.0
    assert bars.iloc[-1]["close"] == 25011.0  # The forming third candle has two minutes.


def test_live_price_updates_the_forming_candle():
    bars = resample_candles(one_minute(TODAY, 12), 5)
    updated = with_live_price(bars, 25100.0, at(TODAY, 9, 26), 5)
    assert len(updated) == 3
    assert updated.iloc[-1]["close"] == 25100.0 and updated.iloc[-1]["high"] == 25100.0
    lower = with_live_price(bars, 24900.0, at(TODAY, 9, 26), 5)
    assert lower.iloc[-1]["low"] == 24900.0


def test_live_price_starts_a_candle_upstox_has_not_sent_yet():
    bars = resample_candles(one_minute(TODAY, 10), 5)
    updated = with_live_price(bars, 25050.0, at(TODAY, 9, 26), 5)
    assert len(updated) == 3
    forming = updated.iloc[-1]
    assert forming["open"] == bars.iloc[-1]["close"] and forming["close"] == 25050.0


def test_no_live_price_leaves_candles_alone():
    bars = resample_candles(one_minute(TODAY, 10), 5)
    assert with_live_price(bars, None, at(TODAY, 9, 26), 5).equals(bars)


class MinuteMarket(Market):
    def __init__(self, today=12):
        super().__init__()
        self.today = today

    def intraday_candles(self, key, minutes):
        if self.error:
            raise self.error
        self.intraday_calls.append((key, minutes))
        return one_minute(TODAY, self.today)

    def minute_candles(self, key, day, minutes):
        if self.error:
            raise self.error
        self.past_calls.append((key, day, minutes))
        return one_minute(day, 375)


def test_one_minute_candles_are_fetched_and_saved(tmp_path):
    market = MinuteMarket()
    data = service(tmp_path, market).intraday("Nifty 50", at(TODAY, 9, 26))
    assert market.intraday_calls == [("NSE_INDEX|Nifty 50", 1)]
    saved = tmp_path / "intraday" / "date=2026-11-11" / "index_Nifty_50.parquet"
    assert saved.exists() and len(pd.read_parquet(saved)) == 12
    assert data["live"] is True and len(data["candles"]) == 3


def test_a_finished_session_is_read_from_disk_without_calling_upstox(tmp_path):
    first = MinuteMarket()
    service(tmp_path, first).intraday("Nifty 50", at(TODAY, 8, 0))
    assert len(first.past_calls) == 1
    second = MinuteMarket()
    data = service(tmp_path, second).intraday("Nifty 50", at(TODAY, 8, 0))
    assert second.past_calls == [] and data["session"] == "2026-11-09"
    assert len(data["candles"]) == 75 and data["live"] is False


def test_a_partly_saved_past_session_is_fetched_again(tmp_path):
    built = service(tmp_path, MinuteMarket())
    built._save_minutes("Nifty 50", date(2026, 11, 9), one_minute(date(2026, 11, 9), 40))
    market = MinuteMarket()
    data = service(tmp_path, market).intraday("Nifty 50", at(TODAY, 8, 0))
    assert len(market.past_calls) == 1 and len(data["candles"]) == 75


def test_saved_candles_are_used_when_upstox_fails(tmp_path):
    service(tmp_path, MinuteMarket()).intraday("Nifty 50", at(TODAY, 9, 26))
    market = MinuteMarket()
    market.error = AuthExpiredError("x")
    data = service(tmp_path, market).intraday("Nifty 50", at(TODAY, 9, 26))
    assert data["status"] == "ok" and len(data["candles"]) == 3


def test_live_feed_price_reaches_the_chart(tmp_path):
    now = at(TODAY, 9, 26, 30)
    write_table(
        tmp_path / "live" / "latest.parquet",
        [{"received_at": now, "instrument_key": "NSE_INDEX|Nifty 50", "ltp": 25123.0}],
    )
    write_status(tmp_path, {"state": "connected", "updated_at": now.isoformat()})
    data = service(tmp_path, MinuteMarket()).intraday("Nifty 50", now)
    assert data["candles"][-1]["close"] == 25123.0


def test_a_stale_live_feed_is_not_used(tmp_path):
    now = at(TODAY, 9, 26, 30)
    write_table(
        tmp_path / "live" / "latest.parquet",
        [{"received_at": now, "instrument_key": "NSE_INDEX|Nifty 50", "ltp": 25123.0}],
    )
    old = now - timedelta(minutes=5)
    write_status(tmp_path, {"state": "connected", "updated_at": old.isoformat()})
    data = service(tmp_path, MinuteMarket()).intraday("Nifty 50", now)
    assert data["candles"][-1]["close"] != 25123.0

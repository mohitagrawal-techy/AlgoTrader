import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from algoverse.brokers.base import MarketDataError
from algoverse.brokers.upstox import BASE_URL
from algoverse.data.calendar import BrokerCalendar, MarketCalendar
from algoverse.data.holidays import (
    HOLIDAYS_PATH,
    fetch_holidays,
    holidays_path,
    load_holidays,
    parse_holidays,
    refresh_holidays,
    save_holidays,
)

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 10, 8, 0, tzinfo=IST)


def epoch(day, hour, minute):
    moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
    return int(moment.timestamp() * 1000)


def nfo(day, start, end):
    return {"exchange": "NFO", "start_time": epoch(day, *start), "end_time": epoch(day, *end)}


# These follow the shapes Upstox returned for 2026.
HOLIDAY = {
    "date": "2026-11-10",
    "description": "Diwali-Balipratipada",
    "holiday_type": "TRADING_HOLIDAY",
    "closed_exchanges": ["NSE", "NFO", "BSE"],
    "open_exchanges": [{"exchange": "MCX", "start_time": 1, "end_time": 2}],
}
SETTLEMENT = {
    "date": "2026-08-26",
    "holiday_type": "SETTLEMENT_HOLIDAY",
    "closed_exchanges": ["CDS", "BCD"],
    "open_exchanges": [nfo(date(2026, 8, 26), (9, 15), (15, 40))],
}
MUHURAT = {
    "date": "2026-11-08",
    "holiday_type": "SPECIAL_TIMING",
    "closed_exchanges": [],
    "open_exchanges": [nfo(date(2026, 11, 8), (14, 20), (15, 40))],
}
MCX_ONLY_CLOSED = {
    "date": "2026-11-12",
    "holiday_type": "TRADING_HOLIDAY",
    "closed_exchanges": ["MCX"],
    "open_exchanges": [],
}
ROWS = [HOLIDAY, SETTLEMENT, MUHURAT]


def at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


def test_holidays_are_read_from_nfo_not_the_holiday_type():
    closed, sessions = parse_holidays([*ROWS, MCX_ONLY_CLOSED])
    assert closed == {date(2026, 11, 10)}
    assert sessions[date(2026, 8, 26)] == (time(9, 15), time(15, 40))
    assert sessions[date(2026, 11, 8)] == (time(14, 20), time(15, 40))
    assert date(2026, 11, 12) not in sessions


def test_calendar_follows_holidays_and_special_sessions():
    calendar = MarketCalendar(*parse_holidays(ROWS))
    holiday, sunday, settlement = date(2026, 11, 10), date(2026, 11, 8), date(2026, 8, 26)
    assert not calendar.is_trading_day(holiday) and not calendar.is_open(at(holiday, 10))
    assert calendar.is_trading_day(sunday)
    assert calendar.is_open(at(sunday, 14, 30)) and not calendar.is_open(at(sunday, 10))
    assert calendar.is_open(at(settlement, 15, 35))
    assert not calendar.is_trading_day(date(2026, 11, 7))  # An ordinary Saturday.
    assert calendar.is_open(at(date(2026, 11, 11), 9, 15))


def test_within_session_widens_the_window():
    calendar = MarketCalendar(*parse_holidays(ROWS))
    margin = timedelta(minutes=15)
    sunday = date(2026, 11, 8)
    assert calendar.within_session(at(sunday, 14, 5), margin, margin)
    assert not calendar.within_session(at(sunday, 14, 4), margin, margin)
    assert calendar.within_session(at(sunday, 15, 55), margin, margin)
    assert not calendar.within_session(at(date(2026, 11, 10), 10), margin, margin)


def test_broker_calendar_picks_up_a_new_list_while_running(tmp_path):
    calendar = BrokerCalendar(tmp_path)
    assert calendar.is_trading_day(date(2026, 11, 10))
    save_holidays(tmp_path, ROWS)
    assert not calendar.is_trading_day(date(2026, 11, 10))
    assert calendar.is_trading_day(date(2026, 11, 8))


def test_manual_holidays_still_apply(tmp_path):
    save_holidays(tmp_path, ROWS)
    calendar = BrokerCalendar(tmp_path, {date(2026, 11, 8), date(2026, 11, 11)})
    assert not calendar.is_trading_day(date(2026, 11, 8))
    assert not calendar.is_trading_day(date(2026, 11, 11))


def test_a_new_year_keeps_earlier_years_and_replaces_its_own(tmp_path):
    save_holidays(tmp_path, ROWS)
    next_year = {**HOLIDAY, "date": "2027-01-26"}
    save_holidays(tmp_path, [next_year])
    closed, _ = load_holidays(tmp_path)
    assert closed == {date(2026, 11, 10), date(2027, 1, 26)}
    save_holidays(tmp_path, [{**HOLIDAY, "date": "2026-12-25"}])
    assert load_holidays(tmp_path)[0] == {date(2026, 12, 25), date(2027, 1, 26)}


def test_refresh_skips_a_recent_copy_and_replaces_an_old_one(tmp_path):
    calls = []

    def fetch():
        calls.append(1)
        return ROWS

    assert refresh_holidays(tmp_path, NOW, fetch) is True
    stamp = NOW.timestamp()
    os.utime(holidays_path(tmp_path), (stamp - 60, stamp - 60))
    assert refresh_holidays(tmp_path, NOW, fetch) is False
    os.utime(holidays_path(tmp_path), (stamp - 13 * 3600, stamp - 13 * 3600))
    assert refresh_holidays(tmp_path, NOW, fetch) is True
    assert len(calls) == 2


def test_failed_refresh_keeps_the_saved_list(tmp_path):
    save_holidays(tmp_path, ROWS)
    stamp = NOW.timestamp() - 13 * 3600
    os.utime(holidays_path(tmp_path), (stamp, stamp))

    def fetch():
        raise MarketDataError("down")

    with pytest.raises(MarketDataError):
        refresh_holidays(tmp_path, NOW, fetch)
    assert load_holidays(tmp_path)[0] == {date(2026, 11, 10)}


def test_unreadable_file_falls_back_to_weekdays(tmp_path):
    holidays_path(tmp_path).write_text("not json")
    assert BrokerCalendar(tmp_path).is_trading_day(date(2026, 11, 10))


@respx.mock
def test_fetch_sends_no_login():
    route = respx.get(f"{BASE_URL}{HOLIDAYS_PATH}").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": ROWS})
    )
    assert fetch_holidays() == ROWS
    assert "authorization" not in route.calls[0].request.headers


@respx.mock
def test_fetch_reports_a_bad_response():
    route = respx.get(f"{BASE_URL}{HOLIDAYS_PATH}")
    route.mock(return_value=httpx.Response(500))
    with pytest.raises(MarketDataError):
        fetch_holidays()
    route.mock(return_value=httpx.Response(200, json={"status": "error"}))
    with pytest.raises(MarketDataError):
        fetch_holidays()

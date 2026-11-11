from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from algoverse.data.options import compact_chains, write_snapshot
from algoverse.market.strike import strike_history

IST = ZoneInfo("Asia/Kolkata")
EXPIRY = "2026-11-17"


def snapshot(tmp_path, day: int, hour: int, minute: int, ltp: float = 100.0):
    ts = datetime(2026, 11, day, hour, minute, tzinfo=IST)
    strikes = [24000.0, 24000.0, 24100.0]
    chain = pd.DataFrame(
        {
            "ts": pd.Timestamp(ts),
            "instrument_key": ["NSE_FO|1", "NSE_FO|2", "NSE_FO|3"],
            "expiry": date.fromisoformat(EXPIRY),
            "strike": strikes,
            "option_type": ["CE", "PE", "CE"],
            "spot": 24050.0,
            "ltp": [ltp, 50.0, 60.0],
            "bid": ltp - 1,
            "ask": ltp + 1,
            "volume": 10,
            "oi": [1000.0, 2000.0, 3000.0],
            "broker_iv": 15.0,
        }
    )
    write_snapshot(tmp_path, "NIFTY", ts, chain, EXPIRY)


def ticks(tmp_path, key: str, times: list[str]):
    folder = tmp_path / "ticks/date=2026-11-11"
    folder.mkdir(parents=True)
    received = pd.to_datetime([f"2026-11-11 {t}" for t in times]).tz_localize(IST).tz_convert("UTC")
    pd.DataFrame(
        {
            "received_at": received,
            "instrument_key": key,
            "ltp": [float(i + 1) for i in range(len(times))],
            "oi": 500.0,
            "iv": 0.2,
            "vtt": range(len(times)),
            "bid_1": 1.0,
            "ask_1": 2.0,
        }
    ).to_parquet(folder / "day.parquet")


def test_snapshots_chart_any_strike_and_list_every_strike(tmp_path):
    snapshot(tmp_path, 11, 9, 20, 100.0)
    snapshot(tmp_path, 11, 9, 25, 110.0)
    result = strike_history(tmp_path, "NIFTY", EXPIRY, 24000, "ce")
    assert result["source"] == "snapshots" and result["day"] == "2026-11-11"
    assert result["strikes"] == [24000.0, 24100.0]
    assert [p["ltp"] for p in result["points"]] == [100.0, 110.0]
    assert result["points"][0]["iv"] == pytest.approx(0.15)
    assert result["instrument_key"] == "NSE_FO|1"


def test_compacted_and_older_days_are_readable(tmp_path):
    snapshot(tmp_path, 10, 9, 20)
    snapshot(tmp_path, 11, 9, 20)
    compact_chains(tmp_path, datetime(2026, 11, 11, 16, 0, tzinfo=IST))
    result = strike_history(tmp_path, "NIFTY", EXPIRY, 24000, "PE", day="2026-11-10")
    assert result["days"] == ["2026-11-11", "2026-11-10"]
    assert result["day"] == "2026-11-10" and len(result["points"]) == 1


def test_feed_ticks_give_one_point_a_minute(tmp_path):
    snapshot(tmp_path, 11, 9, 20)
    ticks(tmp_path, "NSE_FO|1", ["09:05:00", "09:20:05", "09:20:40", "09:21:10"])
    result = strike_history(tmp_path, "NIFTY", EXPIRY, 24000, "CE")
    assert result["source"] == "ticks"
    # The pre-open tick repeats yesterday's close and is left out.
    assert [p["ltp"] for p in result["points"]] == [3.0, 4.0]


def test_snapshots_before_the_open_are_left_out(tmp_path):
    snapshot(tmp_path, 11, 8, 0, 90.0)
    snapshot(tmp_path, 11, 9, 20, 100.0)
    result = strike_history(tmp_path, "NIFTY", EXPIRY, 24000, "CE")
    assert [p["ltp"] for p in result["points"]] == [100.0]


def test_unknown_strike_and_bad_input(tmp_path):
    snapshot(tmp_path, 11, 9, 20)
    assert strike_history(tmp_path, "NIFTY", EXPIRY, 99999, "CE")["points"] == []
    assert strike_history(tmp_path, "BANKNIFTY", EXPIRY, 24000, "CE")["day"] is None
    with pytest.raises(ValueError):
        strike_history(tmp_path, "NIFTY", EXPIRY, 24000, "XX")
    with pytest.raises(ValueError):
        strike_history(tmp_path, "../x", EXPIRY, 24000, "CE")

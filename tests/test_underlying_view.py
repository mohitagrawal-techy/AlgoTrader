from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest
from algoverse.analytics.regime import rsi
from algoverse.data.options import write_snapshot
from algoverse.market.strike import buildup_steps
from algoverse.market.underlying import previous_session, underlying_view

IST = ZoneInfo("Asia/Kolkata")
EXPIRY = "2026-11-17"


def snapshot(tmp_path, day: int, hour: int, minute: int, spot: float, oi=(100.0, 150.0)):
    ts = datetime(2026, 11, day, hour, minute, tzinfo=IST)
    chain = pd.DataFrame(
        {
            "ts": pd.Timestamp(ts),
            "instrument_key": ["NSE_FO|1", "NSE_FO|2"],
            "expiry": date.fromisoformat(EXPIRY),
            "strike": 24000.0,
            "option_type": ["CE", "PE"],
            "spot": spot,
            "ltp": 10.0,
            "oi": list(oi),
        }
    )
    write_snapshot(tmp_path, "NIFTY", ts, chain, EXPIRY)


def write_ticks(tmp_path, day: str, rows: list[dict]):
    folder = tmp_path / f"ticks/date={day}"
    folder.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame["received_at"] = (
        pd.to_datetime(day + " " + frame.pop("time")).dt.tz_localize(IST).dt.tz_convert("UTC")
    )
    frame.to_parquet(folder / "day.parquet")


def no_candles(_name):
    return pd.DataFrame()


def tick(time, instrument, symbol, ltp, atp=None, expiry=None):
    return {
        "time": time,
        "instrument": instrument,
        "symbol": symbol,
        "ltp": ltp,
        "atp": atp,
        "expiry": expiry,
    }


def test_spot_vwap_vix_and_levels_from_ticks(tmp_path):
    snapshot(tmp_path, 11, 9, 20, 24000.0)
    rows = []
    for minute in range(15, 45):
        clock = f"09:{minute:02d}:30"
        spot = 24000.0 + minute
        rows.append(tick(clock, "INDEX", "NIFTY", spot))
        # The future trades 50 points above spot, with its own VWAP.
        rows.append(tick(clock, "FUT", "NIFTY", spot + 50, atp=24070.0, expiry=date(2026, 11, 24)))
        rows.append(tick(clock, "INDEX", "INDIAVIX", 15.0))
    rows.append(tick("09:05:00", "INDEX", "NIFTY", 23000.0))  # Pre-open, left out.
    write_ticks(tmp_path, "2026-11-11", rows)
    view = underlying_view(tmp_path, "NIFTY", no_candles, expiry=EXPIRY)
    assert view["source"] == "ticks" and view["interval_minutes"] == 1
    points = view["points"]
    assert len(points) == 30 and points[0]["spot"] == 24015.0
    # The futures VWAP is moved onto the spot chart by the futures premium.
    assert points[0]["vwap"] == pytest.approx(24020.0)
    assert points[0]["ema_fast"] is None and points[8]["ema_fast"] is not None
    assert view["levels"]["or_high"] == 24029.0 and view["levels"]["or_low"] == 24015.0
    assert view["levels"]["day_high"] == 24044.0
    assert len(view["vix"]) == 30
    assert view["pcr"] == [{"t": view["pcr"][0]["t"], "pcr": 1.5}]


def test_snapshots_stand_in_without_ticks(tmp_path):
    snapshot(tmp_path, 11, 9, 20, 24000.0)
    snapshot(tmp_path, 11, 9, 25, 24010.0)
    view = underlying_view(tmp_path, "NIFTY", no_candles)
    assert view["source"] == "snapshots" and view["interval_minutes"] == 5
    assert [p["spot"] for p in view["points"]] == [24000.0, 24010.0]
    assert view["points"][0]["vwap"] is None and view["vix"] == []
    assert "or_high" not in view["levels"]  # The first 15 minutes are not over in the data.


def test_holidays_are_not_the_previous_session(tmp_path):
    snapshot(tmp_path, 9, 10, 0, 23900.0)
    snapshot(tmp_path, 9, 14, 0, 24050.0)
    # The collector ran on the 10th, a holiday, and saw a frozen spot.
    snapshot(tmp_path, 10, 10, 0, 24050.0)
    snapshot(tmp_path, 10, 14, 0, 24050.0)
    snapshot(tmp_path, 11, 9, 20, 24000.0)
    found = previous_session(tmp_path, "NIFTY", "2026-11-11", no_candles)
    assert found == {"day": "2026-11-09", "high": 24050.0, "low": 23900.0, "close": 24050.0}


def test_after_close_snapshots_are_left_out(tmp_path):
    snapshot(tmp_path, 11, 9, 20, 24000.0)
    snapshot(tmp_path, 11, 18, 0, 24000.0)
    view = underlying_view(tmp_path, "NIFTY", no_candles, expiry=EXPIRY)
    assert len(view["points"]) == 1 and len(view["pcr"]) == 1


def test_previous_session_prefers_candles_then_saved_spot(tmp_path):
    snapshot(tmp_path, 10, 10, 0, 23900.0)
    snapshot(tmp_path, 10, 14, 0, 24100.0)
    snapshot(tmp_path, 11, 9, 20, 24000.0)
    old = pd.DataFrame(
        {"ts": [pd.Timestamp("2026-11-09", tz=IST)], "high": [1.0], "low": [0.5], "close": [0.8]}
    )
    # Candles stop before the 10th, so the 10th's saved prices are used.
    assert previous_session(tmp_path, "NIFTY", "2026-11-11", lambda _n: old) == {
        "day": "2026-11-10",
        "high": 24100.0,
        "low": 23900.0,
        "close": 24100.0,
    }
    current = pd.DataFrame(
        {
            "ts": [pd.Timestamp("2026-11-10", tz=IST)],
            "high": [24200.0],
            "low": [23800.0],
            "close": [24150.0],
        }
    )
    found = previous_session(tmp_path, "NIFTY", "2026-11-11", lambda _n: current)
    assert found["high"] == 24200.0
    view = underlying_view(tmp_path, "NIFTY", lambda _n: current, day="2026-11-11")
    assert view["levels"]["prev_close"] == 24150.0
    assert view["levels"]["pivot_p"] == pytest.approx((24200 + 23800 + 24150) / 3)


def test_rsi_bounds():
    rising = pd.Series(np.arange(30, dtype=float))
    assert rsi(rising).iloc[-1] == 100.0
    mixed = pd.Series([1, 2] * 10, dtype=float)
    # Equal gains and losses sit either side of 50, depending on the latest move.
    assert 50 < rsi(mixed).iloc[-1] < 60 and 40 < rsi(mixed).iloc[-2] < 50
    assert rsi(mixed).iloc[:14].isna().all() and not np.isnan(rsi(mixed).iloc[14])
    # A textbook check: 14 equal rises then one equal fall.
    steps = pd.Series(np.r_[np.arange(15.0), 13.0])
    assert rsi(steps).iloc[-1] == pytest.approx(100 - 100 / (1 + (13 / 14) / (1 / 14)))


def test_buildup_labels_each_block():
    minute = 60_000
    points = [
        {"t": 0, "ltp": 10.0, "oi": 100.0},
        {"t": 15 * minute, "ltp": 12.0, "oi": 150.0},
        {"t": 30 * minute, "ltp": 11.0, "oi": 180.0},
        {"t": 45 * minute, "ltp": 13.0, "oi": 120.0},
        {"t": 60 * minute, "ltp": 12.0, "oi": 100.0},
    ]
    labels = [step["label"] for step in buildup_steps(points)]
    assert labels == ["long_buildup", "short_buildup", "short_covering", "long_unwinding"]


def test_rsi_uses_finished_five_minute_closes(tmp_path):
    snapshot(tmp_path, 11, 9, 20, 24000.0)
    rows = [
        tick(f"{9 + (15 + m) // 60:02d}:{(15 + m) % 60:02d}:30", "INDEX", "NIFTY", 24000.0 + m)
        for m in range(100)
    ]
    write_ticks(tmp_path, "2026-11-11", rows)
    points = underlying_view(tmp_path, "NIFTY", no_candles)["points"]
    rsi_values = [p["rsi"] for p in points]
    # 15 five-minute closes are needed for the first value, at the end of the 15th block.
    first = next(i for i, v in enumerate(rsi_values) if v is not None)
    assert first == 74 and rsi_values[first] == 100.0
    # Within a block the value stays at the last finished block's.
    assert rsi_values[75:79] == [100.0] * 4


def test_bad_input_is_refused(tmp_path):
    with pytest.raises(ValueError):
        underlying_view(tmp_path, "../x", no_candles)
    with pytest.raises(ValueError):
        underlying_view(tmp_path, "NIFTY", no_candles, expiry="soon")

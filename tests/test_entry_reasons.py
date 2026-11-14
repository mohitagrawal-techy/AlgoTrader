"""Why a trade was taken: each entry rule's reading at the decision, and session-anchored rules
that must not read an earlier session's candles."""

from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
from algoverse.lab.spec import StrategySpec, entry_reasons, quick_failures
from algoverse.lab.technical import CompareRule, Frames, Operand, rule_failure, rule_reading
from conftest import IST


def minutes(day: str, start: str, closes) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    ts = pd.date_range(f"{day} {start}", periods=len(closes), freq="1min", tz=IST)
    return pd.DataFrame(
        {"ts": ts, "open": closes, "high": closes + 1, "low": closes - 1, "close": closes}
    )


def daily() -> pd.DataFrame:
    ts = pd.bdate_range("2026-10-01", periods=20).tz_localize(IST)
    return pd.DataFrame({"ts": ts, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0})


BREAKOUT = CompareRule(
    timeframe="15m",
    left=Operand(kind="indicator", name="close"),
    op="above",
    right=Operand(
        kind="indicator", name="opening_range_body", params={"minutes": 15}, field="high"
    ),
)


def test_the_reading_names_the_values_and_the_candle():
    # 09:15 to 09:59: a flat opening range at 100, then a 15-minute candle closing at 120.
    bars = minutes("2026-11-06", "09:15", [100.0] * 30 + [120.0] * 15)
    frames = Frames(daily(), bars)
    reading = rule_reading(BREAKOUT, frames)
    assert reading["holds"] is True
    assert reading["left"] == 120.0 and reading["right"] == 100.0
    assert reading["op"] == "above" and reading["left_name"] == "close"
    # The 09:45 candle, as IST wall-clock seconds.
    stamp = pd.Timestamp("2026-11-06 09:45")
    assert reading["candle"] == int((stamp - pd.Timestamp(0)).total_seconds())


def test_a_late_session_does_not_read_the_last_sessions_range():
    earlier = minutes("2026-11-06", "09:15", [100.0] * 30 + [120.0] * 345)
    # A special session that opens at 14:20: at 14:30 no 15-minute candle of it has finished.
    late = minutes("2026-11-08", "14:20", [130.0] * 10)
    frames = Frames(daily(), pd.concat([earlier, late], ignore_index=True))
    assert "no finished candle this session" in rule_failure(BREAKOUT, frames)


def test_a_session_missing_its_minutes_is_not_traded():
    spec = StrategySpec(
        id="orb",
        name="ORB",
        decide_every="15m",
        legs=[{"action": "buy", "option_type": "CE", "select": "atm_offset", "value": 0}],
        entry={"rules": [BREAKOUT.model_dump()], "dte_min": 3},
    )
    stale = minutes("2026-11-06", "09:15", [100.0] * 30 + [120.0] * 345)
    now = datetime(2026, 11, 9, 10, 0, tzinfo=IST)
    failures = quick_failures(spec, now, lambda: Frames(daily(), stale))
    assert failures == ["entry rule: no one-minute candles for this session"]


def test_entry_reasons_list_every_rule_that_held():
    spec = StrategySpec(
        id="orb",
        name="ORB",
        decide_every="15m",
        legs=[{"action": "buy", "option_type": "CE", "select": "atm_offset", "value": 0}],
        entry={"rules": [BREAKOUT.model_dump()], "dte_min": 3, "time_from": "09:30"},
    )
    bars = minutes("2026-11-06", "09:15", [100.0] * 30 + [120.0] * 15)
    ctx = SimpleNamespace(
        bars=daily(), intraday=bars, now=datetime(2026, 11, 6, 10, 0, tzinfo=IST), regime=None
    )
    reasons = entry_reasons(spec, ctx)
    assert reasons[0] == {
        "rule": "entry time between 09:30 and 15:25",
        "holds": True,
        "value_text": "10:00",
    }
    assert reasons[1]["left"] == 120.0 and reasons[1]["right"] == 100.0


def test_replays_name_each_case_in_trade_order():
    from algoverse.lab.replay import case_names

    leg = {"action": "buy", "option_type": "PE", "select": "atm_offset", "value": 0}
    spec = StrategySpec(
        id="orb",
        name="ORB",
        bias="bullish",
        legs=[{**leg, "option_type": "CE"}],
        cases=[{"name": "Put side", "bias": "bearish", "legs": [leg]}],
    )
    assert case_names(spec) == [
        {"name": "Entry rules", "bias": "bullish"},
        {"name": "Put side", "bias": "bearish"},
    ]

"""Any-of entry rules, Heikin-Ashi candles and the added indicators."""

import numpy as np
import pytest
from algoverse.lab.spec import StrategySpec, describe_entry, entry_failures, signature
from algoverse.lab.technical import (
    CompareRule,
    Frames,
    PatternRule,
    catalog,
    heikin_ashi,
    rule_failure,
    rule_text,
)
from pydantic import ValidationError
from test_lab import SPREAD
from test_technical_rules import candles, ind, num

# The signature a strategy with one entry and one exit rule had before these settings existed.
RULE = {
    "type": "compare",
    "timeframe": "1d",
    "left": {"name": "close"},
    "op": "above",
    "right": {"name": "ema", "params": {"period": 20}},
}
OLD_SIGNATURE = "7f4028146bb3"


def make(entry=None, exits=None, **changes):
    return StrategySpec.model_validate(
        {
            "id": "s1",
            **SPREAD,
            "entry": {**SPREAD["entry"], **(entry or {})},
            "exits": {**SPREAD["exits"], **(exits or {})},
            **changes,
        }
    )


class Ctx:
    def __init__(self, bars):
        self.bars = bars
        self.regime = None
        self.iv = None


def test_heikin_ashi_values():
    bars = candles([110.0, 120.0], opens=[100.0, 110.0], highs=[115.0, 125.0], lows=[95.0, 105.0])
    ha = heikin_ashi(bars)
    assert ha["close"].tolist() == [105.0, 115.0]
    assert ha["open"].tolist() == [105.0, 105.0]
    assert ha["high"].tolist() == [115.0, 125.0]
    assert ha["low"].tolist() == [95.0, 105.0]


def test_heikin_ashi_rule_reads_smoothed_candles():
    # A red candle in a strong uptrend: normal candles say red, Heikin-Ashi still green.
    closes = [100.0 + 10 * i for i in range(10)] + [185.0]
    opens = [c - 5 for c in closes[:-1]] + [195.0]
    bars = candles(closes, opens=opens)
    green = {"left": ind("close"), "op": "above", "right": ind("open")}
    assert rule_failure(CompareRule(**green), Frames(bars)) is not None
    assert rule_failure(CompareRule(candles="heikin_ashi", **green), Frames(bars)) is None
    assert "Heikin-Ashi" in rule_text(CompareRule(candles="heikin_ashi", **green))
    pattern = PatternRule(pattern="doji", candles="heikin_ashi")
    assert rule_text(pattern).startswith("daily Heikin-Ashi")


def test_new_indicators_known_values():
    closes = np.arange(1.0, 31.0)
    bars = candles(closes, highs=closes + 1, lows=closes - 1)
    frames = Frames(bars)
    # Weighted average of 26..30 with weights 1..5.
    assert frames.value("1d", ind("wma", period=5)) == pytest.approx(
        np.dot(np.arange(26, 31), np.arange(1, 6)) / 15
    )
    assert frames.value("1d", ind("donchian", "upper", period=5)) == 31
    assert frames.value("1d", ind("donchian", "lower", period=5)) == 25
    assert frames.value("1d", ind("donchian", "middle", period=5)) == 28
    # Closing one point under the 14-candle high of a 15-point range.
    assert frames.value("1d", ind("williams_r", period=14)) == pytest.approx(-100 / 15)
    # A steady climb sits well above +100.
    assert frames.value("1d", ind("cci", period=20)) > 100
    names = {item["name"] for item in catalog()["indicators"]}
    assert {"wma", "cci", "williams_r", "donchian"} <= names
    assert [c["value"] for c in catalog()["candle_types"]] == ["normal", "heikin_ashi"]


def test_any_of_rules_need_one_to_hold():
    bars = candles([100.0 + i for i in range(30)])
    up = {**RULE, "right": {"kind": "value", "value": 0}}
    down = {**RULE, "op": "below", "right": {"kind": "value", "value": 0}}
    none_hold = make(entry={"any_rules": [down, down]})
    one_holds = make(entry={"any_rules": [down, up]})
    assert "entry rule: none of the any-of rules holds" in entry_failures(none_hold, Ctx(bars))
    assert entry_failures(one_holds, Ctx(bars)) == []
    assert "any of:" in describe_entry(one_holds)


def test_minute_any_of_rules_need_intraday_decisions():
    with pytest.raises(ValidationError):
        make(entry={"any_rules": [{**RULE, "timeframe": "15m"}]})


def test_saved_strategies_keep_their_signature():
    spec = make(entry={"rules": [RULE]}, exits={"rules": [RULE]})
    assert signature(spec) == OLD_SIGNATURE
    with_ha = make(entry={"rules": [{**RULE, "candles": "heikin_ashi"}]}, exits={"rules": [RULE]})
    assert signature(with_ha) != OLD_SIGNATURE
    with_any = make(entry={"rules": [RULE], "any_rules": [RULE]}, exits={"rules": [RULE]})
    assert signature(with_any) != OLD_SIGNATURE


def test_rule_without_candle_type_is_normal():
    assert CompareRule(left=ind("close"), op="above", right=num(1)).candles == "normal"


# ---- Opening range --------------------------------------------------------------------------


def minute_bars(day="2026-11-10", start=100.0, step=0.1, minutes=60):
    import pandas as pd

    ts = pd.date_range(f"{day} 09:15", periods=minutes, freq="1min", tz="Asia/Kolkata")
    close = start + np.arange(minutes) * step
    return pd.DataFrame(
        {"ts": ts, "open": close, "high": close + 1, "low": close - 1, "close": close}
    )


def test_opening_range_is_known_only_after_it_ends():
    frames = Frames(candles([100.0] * 5), minute_bars())
    high = frames.series("5m", ind("opening_range", "high", minutes=15)).tolist()
    low = frames.series("5m", ind("opening_range", "low", minutes=15)).tolist()
    # Candles at 09:15, 09:20 and 09:25 form the range; it is read from the 09:30 candle.
    assert np.isnan(high[:3]).all() and np.isnan(low[:3]).all()
    assert high[3] == pytest.approx(102.4) and low[3] == pytest.approx(99.0)
    # Hourly candles are longer than the range, so there is no value.
    assert np.isnan(frames.series("1h", ind("opening_range", "high", minutes=15))).all()


def test_opening_range_resets_each_session():
    import pandas as pd

    bars = pd.concat([minute_bars("2026-11-10"), minute_bars("2026-11-11", start=200.0)])
    frames = Frames(candles([100.0] * 5), bars.reset_index(drop=True))
    high = frames.series("5m", ind("opening_range", "high", minutes=15)).tolist()
    assert high[3] == pytest.approx(102.4) and high[-1] == pytest.approx(202.4)


def test_breakout_rule_and_daily_refusal():
    breakout = CompareRule(
        timeframe="5m",
        left=ind("close"),
        op="above",
        right=ind("opening_range", "high", minutes=15),
    )
    # A steady climb closes above the first 15 minutes' high soon after.
    assert rule_failure(breakout, Frames(candles([100.0] * 5), minute_bars(step=0.3))) is None
    with pytest.raises(ValidationError):
        CompareRule(left=ind("close"), op="above", right=ind("opening_range", "high", minutes=15))

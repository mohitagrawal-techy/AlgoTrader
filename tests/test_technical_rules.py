import json

import numpy as np
import pandas as pd
import pytest
from algoverse.backtest.dataview import History
from algoverse.backtest.engine import ReplaySpec, replay
from algoverse.lab.spec import CustomSet, StrategySpec, apply_specs, describe_entry, signature
from algoverse.lab.technical import (
    INDICATORS,
    PATTERNS,
    CompareRule,
    Frames,
    Operand,
    PatternRule,
    catalog,
    resample,
    rule_failure,
    rule_text,
)
from conftest import IST
from test_lab import SPREAD, lab_dirs, spec  # noqa: F401


def candles(closes, opens=None, highs=None, lows=None, start="2026-01-05"):
    closes = np.asarray(closes, dtype=float)
    opens = closes if opens is None else np.asarray(opens, dtype=float)
    highs = np.maximum(opens, closes) + 1 if highs is None else np.asarray(highs, dtype=float)
    lows = np.minimum(opens, closes) - 1 if lows is None else np.asarray(lows, dtype=float)
    ts = pd.bdate_range(start, periods=len(closes)).tz_localize(IST)
    return pd.DataFrame({"ts": ts, "open": opens, "high": highs, "low": lows, "close": closes})


def ind(name, field=None, bars_ago=0, **params):
    return Operand(kind="indicator", name=name, params=params, field=field, bars_ago=bars_ago)


def num(value):
    return Operand(kind="value", value=value)


def holds(rule, bars):
    return rule_failure(rule, Frames(bars)) is None


def test_every_indicator_computes_on_real_looking_data():
    rng = np.random.default_rng(3)
    closes = 20000 + np.cumsum(rng.normal(5, 80, 300))
    frames = Frames(candles(closes, opens=closes - rng.normal(0, 40, 300)))
    for name, indicator in INDICATORS.items():
        fields = list(indicator.fields) or [None]
        for field in fields:
            value = frames.value("1d", ind(name, field))
            assert value is not None and np.isfinite(value), (name, field)


def test_moving_average_crossover_is_seen_only_on_the_crossing_candle():
    closes = [100.0] * 30 + [130.0] + [130.0] * 5
    rule = CompareRule(left=ind("close"), op="crosses_above", right=ind("sma", period=20))
    assert holds(rule, candles(closes[:31]))
    assert not holds(rule, candles(closes[:32]))
    above = CompareRule(left=ind("close"), op="above", right=ind("sma", period=20))
    assert holds(above, candles(closes[:32]))


def test_rsi_level_and_bars_ago():
    rising = candles(np.arange(100, 140, dtype=float))
    assert holds(CompareRule(left=ind("rsi", period=14), op="above", right=num(70)), rising)
    earlier = CompareRule(left=ind("close"), op="above", right=ind("close", bars_ago=5))
    assert holds(earlier, rising)


def test_breakout_of_the_prior_twenty_candle_high():
    closes = list(np.linspace(100, 105, 25)) + [120.0]
    rule = CompareRule(left=ind("close"), op="above", right=ind("prior_high", period=20))
    assert holds(rule, candles(closes)) and not holds(rule, candles(closes[:-1]))


def test_supertrend_turns_up_in_a_rally():
    closes = list(np.linspace(200, 150, 40)) + list(np.linspace(150, 230, 40))
    rule = CompareRule(
        left=ind("supertrend", "direction", period=10, multiplier=3), op="above", right=num(0)
    )
    assert holds(rule, candles(closes)) and not holds(rule, candles(closes[:40]))


@pytest.mark.parametrize(
    ("pattern", "opens", "closes", "highs", "lows"),
    [
        ("bullish_engulfing", [100, 104, 97], [100, 98, 106], [101, 105, 107], [99, 97, 96]),
        ("bearish_engulfing", [100, 98, 105], [100, 104, 96], [101, 105, 106], [99, 97, 95]),
        ("hammer", [100, 100, 99], [100, 99, 100], [101, 101, 100.2], [99, 98, 95]),
        ("inside_bar", [100, 100, 101], [100, 104, 102], [101, 106, 105], [99, 98, 99]),
        ("doji", [100, 100, 100], [100, 102, 100.05], [101, 103, 102], [99, 99, 98]),
    ],
)
def test_candle_patterns(pattern, opens, closes, highs, lows):
    bars = candles(closes, opens, highs, lows)
    assert PATTERNS[pattern].detect(bars).iloc[-1]
    assert holds(PatternRule(pattern=pattern), bars)
    assert not holds(PatternRule(pattern=pattern, present=False), bars)


def test_pattern_within_the_last_candles():
    bars = candles(
        [100, 98, 106, 107, 108],
        [100, 104, 97, 106, 107],
        [101, 105, 107, 108, 109],
        [99, 97, 96, 105, 106],
    )
    assert not holds(PatternRule(pattern="bullish_engulfing"), bars)
    assert holds(PatternRule(pattern="bullish_engulfing", within=3), bars)


def test_weekly_and_monthly_candles():
    bars = candles(np.arange(1, 31, dtype=float), start="2026-01-05")
    weekly = resample(bars, "1w")
    assert len(weekly) == 6
    first = weekly.iloc[0]
    assert (first["open"], first["close"], first["high"]) == (1.0, 5.0, 6.0)
    monthly = resample(bars, "1mo")
    assert list(monthly["close"]) == [20.0, 30.0]
    rule = CompareRule(timeframe="1w", left=ind("close"), op="above", right=ind("prev_close"))
    assert holds(rule, bars)


def test_too_little_history_is_reported_not_guessed():
    rule = CompareRule(left=ind("ema", period=50), op="above", right=num(0))
    failure = rule_failure(rule, Frames(candles([100.0] * 10)))
    assert failure and "not available" in failure


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "compare",
            "left": {"kind": "value", "value": 1},
            "op": "above",
            "right": {"kind": "value", "value": 2},
        },
        {
            "type": "compare",
            "left": {"name": "nope"},
            "op": "above",
            "right": {"kind": "value", "value": 2},
        },
        {
            "type": "compare",
            "left": {"name": "rsi", "params": {"period": 1}},
            "op": "above",
            "right": {"kind": "value", "value": 2},
        },
        {
            "type": "compare",
            "left": {"name": "macd"},
            "op": "above",
            "right": {"kind": "value", "value": 0},
        },
        {
            "type": "compare",
            "timeframe": "5m",
            "left": {"name": "close"},
            "op": "above",
            "right": {"kind": "value", "value": 0},
        },
        {"type": "pattern", "pattern": "unicorn"},
    ],
)
def test_invalid_rules_are_refused(payload):
    with pytest.raises(ValueError):
        spec(entry={"dte_min": 3, "dte_max": 20, "rules": [payload]})


def test_rules_join_the_strategy_text_and_signature():
    rule = {
        "type": "compare",
        "timeframe": "1w",
        "left": {"name": "ema", "params": {"period": 9}},
        "op": "crosses_above",
        "right": {"name": "ema", "params": {"period": 21}},
    }
    with_rule = spec(entry={"dte_min": 3, "dte_max": 20, "rules": [rule]})
    assert "weekly EMA(9) crosses above EMA(21)" in describe_entry(with_rule)
    assert signature(with_rule) != signature(spec())
    # Strategies without rules keep the fingerprint they had before rules existed.
    assert signature(spec()) == signature(StrategySpec.model_validate({"id": "s1", **SPREAD}))
    json.dumps(catalog())


def test_rule_text():
    rule = PatternRule(timeframe="1mo", pattern="inside_bar", within=2, present=False)
    assert rule_text(rule) == "no monthly inside bar in the last 2 candles"


def test_backtest_obeys_candle_rules(lab_dirs):  # noqa: F811
    from algoverse.backtest.walkforward import load_services

    data, config_dir = lab_dirs
    sessions = History(data).sessions
    window = ReplaySpec(sessions[60], sessions[-1], 0.0)

    def trades_with(rules):
        strategy = spec(entry={"dte_min": 3, "dte_max": 20, "rules": rules})
        services = apply_specs(load_services(config_dir), [strategy])
        return replay(History(data), services, window, custom=CustomSet([strategy])).trades

    free = trades_with([])
    up_day = {
        "type": "compare",
        "left": {"name": "change_pct"},
        "op": "above",
        "right": {"kind": "value", "value": 0},
    }
    filtered = trades_with([up_day])
    never = trades_with(
        [
            {
                "type": "compare",
                "left": {"name": "close"},
                "op": "below",
                "right": {"kind": "value", "value": 0},
            }
        ]
    )
    assert len(free) > len(filtered) > 0 and never.empty
    # Entries happen the session after an up day: the signal used that day's own candle.
    bars = History(data).bars("NIFTY")
    closes = dict(zip(pd.to_datetime(bars["ts"]).dt.date, bars["close"], strict=True))
    days = sorted(closes)
    for opened in filtered["opened"]:
        decided = days[days.index(opened) - 1]
        before = days[days.index(decided) - 1]
        assert closes[decided] > closes[before]

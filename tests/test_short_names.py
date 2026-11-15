"""Short names for indicators and rules, as the tuner lists them."""

import pytest
from algoverse.lab.short_names import short_operand, short_rule
from algoverse.lab.technical import CompareRule, Operand


@pytest.mark.parametrize(
    ("operand", "name"),
    [
        ({"name": "ema", "params": {"period": 50}}, "EMA 50"),
        ({"name": "macd", "field": "signal"}, "MACD 12/26/9 signal"),
        ({"name": "bbands", "field": "upper"}, "BB 20/2 upper"),
        ({"name": "session_avg", "field": "avg"}, "VWAP"),
        ({"name": "prior_high", "params": {"period": 10}}, "10-candle high"),
        ({"name": "prev_day", "field": "high"}, "Yesterday high"),
        ({"name": "candle_at", "field": "close"}, "09:20 candle close"),
        ({"name": "rsi", "bars_ago": 1}, "RSI 14, 1 ago"),
        (
            {"name": "opening_range_body", "field": "close", "timeframe": "15m"},
            "First 15m close (15m)",
        ),
        ({"kind": "value", "value": 30}, "30"),
    ],
)
def test_short_operand(operand, name):
    assert short_operand(Operand(**operand)) == name


def test_short_rule():
    rule = CompareRule(
        type="compare",
        timeframe="5m",
        left={"name": "low"},
        op="below",
        right={"name": "opening_range_body", "field": "open", "timeframe": "15m"},
    )
    assert short_rule(rule) == "(5m) Low below First 15m open (15m)"

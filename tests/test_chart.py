from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from algoverse.lab.chart import ChartRequest, chart, rule_series
from algoverse.lab.technical import CompareRule, Frames, PatternRule, rule_failure
from conftest import IST, make_bars


def minutes(days=12, seed=5):
    rng = np.random.default_rng(seed)
    rows = []
    for day in pd.bdate_range("2026-10-01", periods=days):
        start = pd.Timestamp(day.date()).tz_localize(IST) + pd.Timedelta(hours=9, minutes=15)
        price = 24000 + rng.normal(0, 50)
        for minute in range(375):
            move = rng.normal(0, 4)
            rows.append(
                {
                    "ts": start + pd.Timedelta(minutes=minute),
                    "open": price,
                    "high": price + abs(move) + 1,
                    "low": price - abs(move) - 1,
                    "close": price + move,
                }
            )
            price += move
    return pd.DataFrame(rows)


RULES = [
    {"left": {"name": "close"}, "op": "above", "right": {"name": "ema", "params": {"period": 10}}},
    {"left": {"name": "rsi"}, "op": "below", "right": {"kind": "value", "value": 45}},
    {
        "left": {"name": "ema", "params": {"period": 5}},
        "op": "crosses_above",
        "right": {"name": "sma", "params": {"period": 20}},
    },
    {
        "left": {"name": "close", "bars_ago": 2},
        "op": "crosses_below",
        "right": {"name": "bbands", "field": "middle"},
    },
]


@pytest.mark.parametrize("timeframe", ["1d", "15m"])
@pytest.mark.parametrize("candles", ["normal", "heikin_ashi"])
def test_signals_match_the_backtest_rule_check_at_every_candle(timeframe, candles):
    daily, mins = make_bars(count=120), minutes()
    rules = [CompareRule(timeframe=timeframe, candles=candles, **rule) for rule in RULES] + [
        PatternRule(timeframe=timeframe, candles=candles, pattern="bullish_engulfing", within=3),
        PatternRule(timeframe=timeframe, candles=candles, pattern="doji", present=False),
    ]
    full = Frames(daily, mins)
    length = len(full.frame(timeframe, candles))
    series = [rule_series(rule, full).reset_index(drop=True) for rule in rules]
    checked = 0
    for end in range(1, length + 1, 3):
        if timeframe == "1d":
            frames = Frames(daily.iloc[:end].reset_index(drop=True), mins)
        else:
            # Minute candles up to the end of the `end`-th 15-minute candle.
            cutoff = full.frame("15m")["ts"].iloc[end - 1] + pd.Timedelta(minutes=15)
            frames = Frames(daily, mins[mins["ts"] < cutoff])
            if len(frames.frame(timeframe, candles)) != end:
                continue
        for rule, held in zip(rules, series, strict=True):
            assert (rule_failure(rule, frames) is None) == bool(held.iloc[end - 1]), (rule, end)
        checked += 1
    assert checked > 20


def request(**changes):
    return ChartRequest.model_validate({"symbol": "NIFTY", **changes})


def test_studies_split_into_price_overlays_and_their_own_panes():
    body = chart(
        make_bars(count=200),
        pd.DataFrame(),
        request(
            studies=[
                {"id": "a", "name": "ema", "params": {"period": 9}},
                {"id": "b", "name": "bbands"},
                {"id": "c", "name": "rsi"},
                {"id": "d", "name": "opening_range"},
                {"id": "e", "name": "sma", "params": {"period": 1000}},
            ]
        ),
    )
    assert len(body["time"]) == len(body["close"]) == 200
    ema, bands, rsi, orb, bad = body["studies"]
    assert ema["label"] == "Exponential moving average (9)" and ema["lines"][0]["overlay"]
    assert len(ema["lines"][0]["values"]) == 200 and ema["lines"][0]["values"][0] is None
    overlays = {line["field"]: line["overlay"] for line in bands["lines"]}
    assert overlays == {"upper": True, "middle": True, "lower": True}
    assert not rsi["lines"][0]["overlay"]
    assert "needs 5-minute" in orb["error"] and "between 2 and 400" in bad["error"]


def test_minute_charts_show_recent_sessions_in_ist_wall_time():
    body = chart(make_bars(count=50), minutes(days=12), request(timeframe="15m", sessions=3))
    assert len(body["time"]) == 3 * 25
    first = datetime.utcfromtimestamp(body["time"][0])
    assert (first.hour, first.minute) == (9, 15)
    assert chart(make_bars(), pd.DataFrame(), request(timeframe="5m"))["empty"]


def test_signal_hits_combine_all_and_any_rules_on_this_timeframe_only():
    always = {
        "type": "compare",
        "left": {"name": "close"},
        "op": "above",
        "right": {"kind": "value", "value": 1},
    }
    never = {
        "type": "compare",
        "left": {"name": "close"},
        "op": "below",
        "right": {"kind": "value", "value": 1},
    }
    body = chart(
        make_bars(count=60),
        pd.DataFrame(),
        request(
            rules=[always, {**always, "timeframe": "1w"}],
            any_rules=[never, {**always, "left": {"name": "rsi"}}],
        ),
    )
    signals = body["signals"]
    assert signals["other_timeframes"] == ["weekly close above 1"]
    counts = {(r["group"], r["text"]): r["count"] for r in signals["rules"]}
    assert counts[("all", "daily close above 1")] == 60
    assert counts[("any", "daily close below 1")] == 0
    # RSI needs 14 candles of warm-up, so the any-group holds from then on.
    assert signals["hits"] == list(range(14, 60))


def test_signal_positions_are_within_the_candles_shown():
    rule = {
        "type": "compare",
        "timeframe": "15m",
        "left": {"name": "close"},
        "op": "above",
        "right": {"name": "ema", "params": {"period": 5}},
    }
    body = chart(make_bars(), minutes(days=12), request(timeframe="15m", sessions=2, rules=[rule]))
    hits = body["signals"]["hits"]
    assert hits and all(0 <= i < len(body["time"]) for i in hits)
    assert len(hits) == body["signals"]["rules"][0]["count"]


def test_minute_charts_read_only_recent_sessions_plus_warm_up():
    from algoverse.lab import chart as chart_module

    mins = minutes(days=12)
    study = [{"id": "a", "name": "ema", "params": {"period": 20}}]
    full = chart(make_bars(), mins, request(timeframe="15m", sessions=12, studies=study))
    old = chart_module.WARMUP_SESSIONS
    try:
        chart_module.WARMUP_SESSIONS = 3
        short = chart(make_bars(), mins, request(timeframe="15m", sessions=2, studies=study))
    finally:
        chart_module.WARMUP_SESSIONS = old
    assert len(short["time"]) == 50 and short["time"] == full["time"][-50:]
    # 75 candles of warm-up leave a 20-candle EMA within a few millionths of the full value.
    tail = full["studies"][0]["lines"][0]["values"][-50:]
    assert short["studies"][0]["lines"][0]["values"] == pytest.approx(tail, rel=1e-5)

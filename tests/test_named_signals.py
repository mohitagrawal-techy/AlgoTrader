import pandas as pd
import pytest
from algoverse.lab.chart import rule_series
from algoverse.lab.spec import StrategySpec, signature
from algoverse.lab.technical import Frames, SignalRule, rule_failure, rule_text, rules_failures
from conftest import make_bars
from test_chart import minutes
from test_lab import SPREAD, spec


def compare(left, op, right, join="and", timeframe="15m"):
    return {
        "type": "compare",
        "join": join,
        "timeframe": timeframe,
        "left": left,
        "op": op,
        "right": right,
    }


EMA = {"name": "ema", "params": {"period": 5}}
SMA = {"name": "sma", "params": {"period": 20}}
FIFTY = {"kind": "value", "value": 50}
ONE = [compare({"name": "close"}, "above", EMA), compare({"name": "rsi"}, "above", FIFTY)]
TWO = [compare({"name": "close"}, "below", SMA), compare({"name": "rsi"}, "below", FIFTY)]


def with_signals(**entry):
    return spec(
        decide_every="15m",
        exits={**SPREAD["exits"], "square_off": "15:15"},
        signals=[{"name": "Buy one", "rules": ONE}, {"name": "Buy two", "rules": TWO}],
        entry={**SPREAD["entry"], **entry},
    )


def test_signals_are_copied_into_the_conditions_that_read_them():
    built = with_signals(
        rules=[
            {"type": "signal", "name": "buy one"},
            {"type": "signal", "name": "Buy two", "join": "or"},
        ]
    )
    first, second = built.entry.rules
    assert isinstance(first, SignalRule) and first.name == "Buy one"
    assert [rule_text(r) for r in first.rules] == [
        rule_text(r) for r in with_flat().entry.rules[:2]
    ]
    assert rule_text(second) == "Buy two is true"
    # The signature follows the copied conditions, so editing a signal changes it.
    changed = with_signals(rules=[{"type": "signal", "name": "Buy one"}])
    assert signature(changed) != signature(built)


def with_flat():
    return spec(
        decide_every="15m",
        exits={**SPREAD["exits"], "square_off": "15:15"},
        entry={**SPREAD["entry"], "rules": ONE + [{**TWO[0], "join": "or"}, TWO[1]]},
    )


def test_unknown_or_repeated_signal_names_are_refused():
    with pytest.raises(ValueError, match="no signal is named"):
        with_signals(rules=[{"type": "signal", "name": "Sell"}])
    with pytest.raises(ValueError, match="same name"):
        spec(signals=[{"name": "A", "rules": ONE}, {"name": "a", "rules": TWO}])


def test_old_strategies_keep_their_signature():
    assert signature(spec()) == signature(StrategySpec.model_validate({"id": "s1", **SPREAD}))


def test_signals_read_as_their_conditions_in_backtests_and_on_the_chart():
    signalled = with_signals(
        rules=[
            {"type": "signal", "name": "Buy one"},
            {"type": "signal", "name": "Buy two", "join": "or"},
        ]
    ).entry.rules
    negated = with_signals(rules=[{"type": "signal", "name": "Buy one", "present": False}])
    flat = with_flat().entry.rules
    daily, mins = make_bars(count=60), minutes(days=8)
    full = Frames(daily, mins)
    stamps = full.frame("15m")["ts"]
    one = rule_series(signalled[0], full).reset_index(drop=True)
    off = rule_series(negated.entry.rules[0], full).reset_index(drop=True)
    checked = held_count = 0
    for end in range(25, len(stamps) + 1, 3):
        cutoff = stamps.iloc[end - 1] + pd.Timedelta(minutes=15)
        frames = Frames(daily, mins[mins["ts"] < cutoff])
        if len(frames.frame("15m")) != end:
            continue
        expected = not rules_failures(flat, frames)
        assert (not rules_failures(signalled, frames)) == expected, end
        first = rule_failure(signalled[0], frames) is None
        assert first == bool(one.iloc[end - 1]) and first != bool(off.iloc[end - 1]), end
        held_count += expected
        checked += 1
    assert checked > 20 and 0 < held_count < checked

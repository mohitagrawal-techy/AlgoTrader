import pytest
from algoverse.lab.spec import StrategySpec, describe_entry, signature, variants
from test_intraday_backtest import intraday_dirs, run  # noqa: F401
from test_lab import SPREAD

NEVER = {
    "type": "compare",
    "timeframe": "15m",
    "left": {"name": "close"},
    "op": "below",
    "right": {"kind": "value", "value": 0},
}
ALWAYS = {**NEVER, "op": "above"}
PUT = [{"action": "buy", "option_type": "PE", "select": "atm_offset", "value": 0}]
INTRADAY = {
    "decide_every": "15m",
    # A tight stop, like an opening-range breakout's, so one lot of a long put fits the
    # per-trade loss limit.
    "exits": {**SPREAD["exits"], "square_off": "15:00", "stop_frac": 0.1},
}


def make(**changes) -> StrategySpec:
    return StrategySpec.model_validate({"id": "s1", **SPREAD, **INTRADAY, **changes})


def case(rules, name="Put", bias="bearish"):
    return {"name": name, "bias": bias, "legs": PUT, "rules": rules}


def test_a_second_case_trades_its_own_legs_when_its_rules_hold(intraday_dirs):  # noqa: F811
    strategy = make(entry={**SPREAD["entry"], "rules": [NEVER]}, cases=[case([ALWAYS])])
    result = run(intraday_dirs, strategy)
    assert result.details
    for trade in result.details:
        assert trade["strategy"] == "s1"
        assert [leg["option_type"] for leg in trade["legs"]] == ["PE"]


def test_cases_share_the_day_trade_limit(intraday_dirs):  # noqa: F811
    strategy = make(
        entry={**SPREAD["entry"], "rules": [ALWAYS], "max_trades_per_day": 1},
        cases=[case([ALWAYS])],
    )
    result = run(intraday_dirs, strategy)
    days = [trade["opened"] for trade in result.details]
    assert days and len(days) == len(set(days))


def test_variants_keep_the_strategy_and_swap_legs_and_rules():
    strategy = make(entry={**SPREAD["entry"], "rules": [ALWAYS]}, cases=[case([NEVER])])
    first, second = variants(strategy)
    assert first.id == second.id == "s1" and not first.cases and not second.cases
    assert first.legs == strategy.legs and second.legs[0].option_type == "PE"
    assert second.entry.rules[0].op == "below" and second.bias == "bearish"
    assert second.entry.dte_min == strategy.entry.dte_min
    assert "; or Put: +1 PE ATM when 15-minute close below 0" in describe_entry(strategy)


def test_cases_change_the_signature_only_when_present():
    plain = make()
    assert signature(plain) == signature(make(cases=[]))
    assert signature(make(cases=[case([ALWAYS])])) != signature(plain)


def test_expiry_day_entries_only_for_strategies_that_square_off():
    entry = {**SPREAD["entry"], "dte_min": 0, "dte_max": 6}
    exits = {**INTRADAY["exits"], "exit_dte": 0}
    assert make(entry=entry, exits=exits).entry.dte_min == 0
    with pytest.raises(ValueError, match="dte_min"):
        StrategySpec.model_validate(
            {"id": "s1", **SPREAD, "entry": entry, "exits": {**SPREAD["exits"], "exit_dte": 0}}
        )
    with pytest.raises(ValueError, match="dte_min"):
        make(entry=entry, exits={**exits, "square_off": None})

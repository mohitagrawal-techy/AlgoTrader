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


def run_with_budget(dirs, strategy, every=15, days=8):
    """Like the lab's own replays: the strategy's budget sets its risk limits."""
    from algoverse.backtest.dataview import History
    from algoverse.backtest.engine import ReplaySpec
    from algoverse.backtest.intraday import replay_intraday
    from algoverse.backtest.walkforward import load_services
    from algoverse.data.candles import MINUTE, CandleStore
    from algoverse.lab.spec import CustomSet, apply_specs, with_budget
    from test_intraday_backtest import NIFTY

    data, config_dir = dirs
    services = with_budget(apply_specs(load_services(config_dir), [strategy]), strategy)
    sessions = History(data).sessions
    return replay_intraday(
        History(data),
        services,
        ReplaySpec(sessions[-days], sessions[-1], 0.0),
        {"NIFTY": CandleStore(data).read(NIFTY, MINUTE)},
        every,
        custom=CustomSet([strategy]),
    )


def test_budget_sizing_buys_as_many_lots_as_the_money_allows(intraday_dirs):  # noqa: F811
    entry = {**SPREAD["entry"], "rules": [ALWAYS], "max_trades_per_day": 1}
    by_risk = run_with_budget(intraday_dirs, make(legs=PUT, entry=entry, budget={"sizing": "risk"}))
    by_budget = run_with_budget(
        intraday_dirs, make(legs=PUT, entry=entry, budget={"sizing": "budget"})
    )
    assert by_risk.details and by_budget.details
    risk_lots = max(t["lots"] for t in by_risk.details)
    for trade in by_budget.details:
        leg = trade["legs"][0]
        cost = leg["entry_price"] * leg["units"] / trade["lots"]
        # Spent up to the ₹1,00,000 budget: one more lot would not have fitted.
        assert trade["lots"] * cost <= 100_000 * 1.2 and (trade["lots"] + 1) * cost > 100_000 * 0.8
    assert max(t["lots"] for t in by_budget.details) > risk_lots


def test_strikes_can_be_picked_by_premium(intraday_dirs):  # noqa: F811
    target = 60.0
    legs = [{**PUT[0], "select": "premium", "value": target}]
    entry = {**SPREAD["entry"], "rules": [ALWAYS], "max_trades_per_day": 1}
    result = run(intraday_dirs, make(legs=legs, entry=entry))
    assert result.details
    prices = [trade["legs"][0]["entry_price"] for trade in result.details]
    # Fills are at the ask, a little above the mid the strike was chosen by.
    assert all(abs(price - target) < target * 0.5 for price in prices)
    from algoverse.lab.spec import describe_legs

    assert describe_legs(make(legs=legs)) == "+1 PE premium near 60"

from datetime import datetime
from types import SimpleNamespace

import pytest
from algoverse.execution.ledger import Position
from algoverse.execution.paper import effective_stop
from algoverse.lab.spec import StrategySpec, signature
from algoverse.risk.config import ExitRule
from algoverse.risk.limits import make_exit_plan
from conftest import IST
from test_intraday_backtest import intraday_dirs, run  # noqa: F401
from test_lab import SPREAD, spec

ALWAYS = {
    "type": "compare",
    "timeframe": "1d",
    "left": {"name": "close"},
    "op": "above",
    "right": {"kind": "value", "value": 0},
}


def plan_for(**rule):
    candidate = SimpleNamespace(profile="p", legs=[SimpleNamespace(lot_size=75)])
    structure = SimpleNamespace(entry_debit=7500.0, bounded=False, max_profit=0, max_loss=0)
    config = SimpleNamespace(
        exits={"p": ExitRule(target_frac=0.5, stop_frac=0.5, exit_dte=1, **rule)},
        stop_gap_buffer=1.0,
    )
    return make_exit_plan(candidate, structure, config)


def test_exit_amounts_in_each_unit():
    assert (plan_for().target_pnl, plan_for().stop_pnl) == (3750.0, -3750.0)
    rupees = plan_for(target_unit="rupees", stop_unit="rupees")
    assert (rupees.target_pnl, rupees.stop_pnl) == (0.5, -0.5)
    plan = plan_for(
        target_unit="points",
        trail=10,
        trail_unit="points",
        breakeven_at=2000,
        breakeven_unit="rupees",
    )
    assert plan.target_pnl == 0.5 * 75 and plan.trail == 750 and plan.breakeven_at == 2000


def position(**fields):
    base = dict(
        symbol="NIFTY",
        strategy="s",
        profile="p",
        lots=1,
        created_at="",
        target_pnl=5000,
        stop_pnl=-2000,
        exit_dte=1,
        planned_loss=2000,
        margin=0,
        score=0,
        expected_entry_debit=0,
        last_spot=0,
    )
    return Position(**(base | fields))


def test_trailing_stop_and_move_to_cost_only_tighten():
    assert effective_stop(position()) == (-2000, "stop")
    assert effective_stop(position(trail_pnl=1500, peak_pnl=0)) == (-2000, "stop")
    assert effective_stop(position(trail_pnl=1500, peak_pnl=3000)) == (1500, "trailing_stop")
    assert effective_stop(position(breakeven_pnl=1000, peak_pnl=800)) == (-2000, "stop")
    assert effective_stop(position(breakeven_pnl=1000, peak_pnl=1200)) == (0.0, "stop_at_cost")
    # Trailing wins once it is tighter than cost.
    both = position(breakeven_pnl=1000, trail_pnl=500, peak_pnl=2000)
    assert effective_stop(both) == (1500, "trailing_stop")


@pytest.mark.parametrize(
    "exits",
    [
        {"target_unit": "rupees", "target_frac": 0.01},
        {"stop_unit": "points", "stop_frac": 20000},
        {"trail": 9.0},
        {"daily_stop": 0},
    ],
)
def test_bad_exit_settings_are_refused(exits):
    with pytest.raises(ValueError):
        spec(exits={**SPREAD["exits"], **exits})


def test_old_strategies_keep_their_signature():
    assert signature(spec()) == signature(StrategySpec.model_validate({"id": "s1", **SPREAD}))


def intraday_spec(**changes):
    entry = {**SPREAD["entry"], **changes.pop("entry", {})}
    exits = {**SPREAD["exits"], "square_off": "15:15", **changes.pop("exits", {})}
    return spec(decide_every="15m", entry=entry, exits=exits, **changes)


def opened(trade):
    return datetime.fromisoformat(trade["opened_at"]).astimezone(IST)


def test_exit_rules_close_trades(intraday_dirs):  # noqa: F811
    result = run(intraday_dirs, intraday_spec(exits={"rules": [ALWAYS]}))
    reasons = {t["exit_reason"] for t in result.details}
    assert "exit_rule" in reasons
    for trade in result.details:
        if trade["exit_reason"] == "exit_rule":
            held = datetime.fromisoformat(trade["closed_at"]) - datetime.fromisoformat(
                trade["opened_at"]
            )
            assert held.total_seconds() <= 15 * 60  # Closed at the first check after entry.


def test_max_trades_per_day(intraday_dirs):  # noqa: F811
    free = run(intraday_dirs, intraday_spec(exits={"rules": [ALWAYS]}))
    capped = run(
        intraday_dirs, intraday_spec(entry={"max_trades_per_day": 1}, exits={"rules": [ALWAYS]})
    )
    per_day = {}
    for trade in capped.details:
        per_day[opened(trade).date()] = per_day.get(opened(trade).date(), 0) + 1
    assert max(per_day.values()) == 1 and len(free.details) > len(capped.details)


def test_daily_target_stops_the_day(intraday_dirs):  # noqa: F811
    result = run(intraday_dirs, intraday_spec(exits={"daily_target": 1, "daily_stop": 1}))
    by_day = {}
    for trade in result.details:
        by_day.setdefault(opened(trade).date(), []).append(trade)
    for trades in by_day.values():
        # Once a daily limit closes the day's trade, nothing else opens that day.
        hits = [t for t in trades if t["exit_reason"] in ("daily_stop", "daily_target")]
        if hits:
            last_close = max(datetime.fromisoformat(t["closed_at"]) for t in hits)
            assert all(datetime.fromisoformat(t["opened_at"]) <= last_close for t in trades)
    assert {t["exit_reason"] for t in result.details} & {"daily_stop", "daily_target"}


def test_no_reentry_after_a_stop(intraday_dirs):  # noqa: F811
    tight = {"stop_unit": "rupees", "stop_frac": 1}
    result = run(intraday_dirs, intraday_spec(entry={"reenter_after_stop": False}, exits=tight))
    by_day = {}
    for trade in sorted(result.details, key=lambda t: t["opened_at"]):
        by_day.setdefault(opened(trade).date(), []).append(trade)
    stops = 0
    for trades in by_day.values():
        for index, trade in enumerate(trades):
            if trade["exit_reason"] in ("stop", "trailing_stop", "stop_at_cost"):
                stops += 1
                assert index == len(trades) - 1  # Nothing after a stop that day.
    assert stops > 0


def test_trailing_stop_in_a_backtest(intraday_dirs):  # noqa: F811
    trail = {"trail": 0.05, "target_frac": 5}
    result = run(intraday_dirs, intraday_spec(exits=trail))
    assert len(result.details) > 0
    assert {t["exit_reason"] for t in result.details} <= {
        "trailing_stop",
        "stop",
        "square_off",
        "target",
        "stop_at_cost",
        "end_of_test",
    }


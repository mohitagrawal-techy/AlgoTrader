from types import SimpleNamespace

import pandas as pd
import pytest
from algoverse.execution.ledger import CLOSED, OPEN, PaperLeg
from algoverse.execution.paper import _leg_levels
from algoverse.lab.spec import CustomSet, StrategySpec, describe_entry, describe_legs, signature
from conftest import NOW, later
from test_cases import ALWAYS, INTRADAY, NEVER, PUT, case, make, run_with_budget
from test_intraday_backtest import intraday_dirs, run  # noqa: F401
from test_lab import SPREAD
from test_paper import EXPIRY, broker, candidate, idea, leg, one  # noqa: F401

PE_KEY = "PE24000"


def quotes(ce=(99.0, 100.0), pe=(99.0, 100.0)):
    rows = []
    for key, (bid, ask) in ((PE_KEY, pe), ("CE24000", ce)):
        rows.append(
            {
                "instrument_key": key,
                "bid": bid,
                "ask": ask,
                "mid": (bid + ask) / 2,
                "bid_qty": 650.0,
                "ask_qty": 650.0,
                "delta": 0.5,
                "gamma": 0.0005,
                "vega": 10.0,
                "spot": 24000.0,
            }
        )
    return {"NIFTY": pd.DataFrame(rows)}


def test_a_leg_closes_on_its_own_stop_and_the_last_leg_closes_the_position(
    broker,  # noqa: F811
):
    from dataclasses import replace

    slip = broker.paper.slippage_per_leg
    call = replace(leg(), stop=20.0)
    put = replace(leg(option_type="PE", key=PE_KEY), target=50.0)
    debit = 2 * 65 * (100.0 + slip)
    broker.submit(idea(cand=candidate((call, put)), debit=debit, stop=-1e6, target=1e6), NOW)
    broker.step(later(5), quotes())
    position = one(broker, OPEN)
    assert position.legs[0].stop_price == pytest.approx((100.0 + slip) * 0.8)
    assert position.legs[1].target_price == pytest.approx((100.0 + slip) * 1.5)

    # The call falls through its stop: it is marked, then fills at the next quotes alone.
    broker.step(later(10), quotes(ce=(70.0, 71.0)))
    broker.step(later(15), quotes(ce=(70.0, 71.0)))
    position = one(broker, OPEN)
    call_leg, put_leg = position.legs
    assert call_leg.exit_reason == "leg_stop" and call_leg.exit_price == pytest.approx(70.0 - slip)
    assert put_leg.exit_price is None
    assert position.exit_costs > 0

    # The put reaches its target and, as the last open leg, closes the position.
    broker.step(later(20), quotes(ce=(50.0, 51.0), pe=(160.0, 161.0)))
    broker.step(later(25), quotes(ce=(50.0, 51.0), pe=(160.0, 161.0)))
    position = one(broker, CLOSED)
    assert position.exit_reason == "leg_target"
    # The call kept its stop fill though it fell further.
    assert position.legs[0].exit_price == pytest.approx(70.0 - slip)
    gross = 65 * ((70.0 - slip) - (100.0 + slip)) + 65 * ((160.0 - slip) - (100.0 + slip))
    assert position.realized_pnl == pytest.approx(
        gross - position.entry_costs - position.exit_costs
    )


def test_a_sold_legs_stop_is_above_its_fill():
    sold = PaperLeg("k", EXPIRY.isoformat(), 24000.0, "CE", -1, 65, entry_price=100.0)
    sold.stop, sold.target, sold.target_unit = 30.0, 20.0, "points"
    assert _leg_levels(sold) == (pytest.approx(130.0), pytest.approx(80.0))
    plain = PaperLeg("k", EXPIRY.isoformat(), 24000.0, "CE", 1, 65, entry_price=100.0)
    assert _leg_levels(plain) == (None, None)


def test_new_settings_leave_old_strategies_alone_and_are_checked():
    plain = make()
    legs = [{**PUT[0], "stop": 20, "target": 40, "target_unit": "points"}]
    with_levels = make(legs=legs)
    assert signature(make(legs=PUT)) != signature(with_levels)
    assert signature(plain) == signature(
        make(
            entry={**SPREAD["entry"], "reenter_after_target": True, "wait_move": None},
            cases=[],
        )
    )
    assert signature(make(cases=[case([ALWAYS])])) == signature(
        make(cases=[{**case([ALWAYS]), "exit_rules": None}])
    )
    assert describe_legs(with_levels) == "+1 PE ATM (SL 20%, TP 40 pts)"
    with pytest.raises(ValueError, match="leg stop"):
        make(legs=[{**PUT[0], "stop": 0.5}])
    waiting = make(entry={**SPREAD["entry"], "wait_move": 10, "wait_direction": "down"})
    assert "wait for the premium to move down 10%" in describe_entry(waiting)
    with pytest.raises(ValueError, match="wait and trade"):
        StrategySpec.model_validate(
            {"id": "s1", **SPREAD, "entry": {**SPREAD["entry"], "wait_move": 10}}
        )


def closed(reason: str, day):
    return SimpleNamespace(
        strategy="s1",
        status="closed",
        created_at=f"{day}T10:00:00+05:30",
        closed_at=f"{day}T11:00:00+05:30",
        exit_reason=reason,
        realized_pnl=100.0,
    )


def test_re_execute_off_stops_new_trades_after_a_target():
    spec = make(entry={**SPREAD["entry"], "reenter_after_target": False})
    custom = CustomSet([spec])
    day = NOW.date()
    custom.broker = SimpleNamespace(
        ledger=SimpleNamespace(positions=lambda statuses: [closed("leg_target", day)])
    )
    assert custom.limit_failures(spec, day) == ["entry rule: no new trade after a target today"]
    custom.broker.ledger.positions = lambda statuses: [closed("leg_stop", day)]
    assert custom.limit_failures(spec, day) == []
    stops_too = make(entry={**SPREAD["entry"], "reenter_after_stop": False})
    assert custom.limit_failures(stops_too, day) == ["entry rule: no re-entry after a stop today"]


DAY_ONE = {**SPREAD["entry"], "rules": [ALWAYS], "max_trades_per_day": 1}


def test_legs_exit_on_their_own_levels_in_a_backtest(intraday_dirs):  # noqa: F811
    legs = [{**PUT[0], "stop": 5, "target": 5}]
    # The position's own stop and target are far away, so the legs' levels act first.
    wide = {**INTRADAY["exits"], "stop_frac": 5.0, "target_frac": 5.0}
    # Sized by budget, so the wide stop does not block the trade.
    result = run_with_budget(intraday_dirs, make(legs=legs, entry=DAY_ONE, exits=wide))
    assert result.details
    reasons = {trade["exit_reason"] for trade in result.details}
    assert reasons <= {"leg_stop", "leg_target", "square_off"} and reasons & {
        "leg_stop",
        "leg_target",
    }
    for trade in result.details:
        only = trade["legs"][0]
        if trade["exit_reason"] == "leg_stop":
            assert only["exit_reason"] == "leg_stop" and only["stop_price"] is not None


def test_a_case_can_have_its_own_exit_rules(intraday_dirs):  # noqa: F811
    entry = {**SPREAD["entry"], "rules": [NEVER], "max_trades_per_day": 1}
    own_exit = {**case([ALWAYS]), "exit_rules": [ALWAYS]}
    result = run(intraday_dirs, make(entry=entry, cases=[own_exit]))
    assert result.details
    assert all(trade["case"] == 1 for trade in result.details)
    assert all(trade["exit_reason"] == "exit_rule" for trade in result.details)
    # Without its own rules the case uses the strategy's, which here has none.
    shared = run(intraday_dirs, make(entry=entry, cases=[case([ALWAYS])]))
    assert shared.details
    assert all(trade["exit_reason"] != "exit_rule" for trade in shared.details)


def test_wait_and_trade_holds_the_entry_until_the_premium_moves(intraday_dirs):  # noqa: F811
    plain = run(intraday_dirs, make(legs=PUT, entry=DAY_ONE))
    never = run(intraday_dirs, make(legs=PUT, entry={**DAY_ONE, "wait_move": 500}))
    assert plain.details and not never.details
    # The test market moves little: a fifth of a point up is enough.
    small = {**DAY_ONE, "wait_move": 0.2, "wait_unit": "points"}
    waited = run(intraday_dirs, make(legs=PUT, entry=small))
    assert waited.details
    first = {trade["opened"]: trade["opened_at"] for trade in plain.details}
    for trade in waited.details:
        assert trade["opened_at"] > first[trade["opened"]]


def test_picked_indicators_are_remembered_but_do_not_change_results():
    picked = {"indicators": [{"name": "ema", "params": {"period": 20}}, {"name": "rsi"}]}
    spec = make(**picked)
    assert [pick.name for pick in spec.indicators] == ["ema", "rsi"]
    # They only shape the editor, so a strategy keeps its id and its stored results.
    assert signature(spec) == signature(make())
    with pytest.raises(ValueError, match="unknown indicator"):
        make(indicators=[{"name": "not_an_indicator"}])
    # Each keeps its own name, candle interval and chart type.
    named = make(
        indicators=[
            {
                "name": "sma",
                "params": {"period": 9},
                "label": "sma",
                "timeframe": "5m",
                "candles": "heikin_ashi",
            }
        ]
    ).indicators[0]
    assert (named.label, named.timeframe, named.candles) == ("sma", "5m", "heikin_ashi")

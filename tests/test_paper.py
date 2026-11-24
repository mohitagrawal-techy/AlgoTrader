from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from algoverse.data.store import SnapshotStore
from algoverse.evaluation.config import EventCalendar
from algoverse.execution.cycle import CycleDeps, run_cycle
from algoverse.execution.ledger import CANCELLED, CLOSED, EXITING, OPEN, PENDING, Ledger
from algoverse.execution.paper import PaperBroker
from algoverse.risk.limits import (
    ExitPlan,
    PortfolioState,
    evaluate_risk,
    make_exit_plan,
    position_key,
)
from algoverse.risk.stress import StressResult, stress_test
from algoverse.strategies.base import BUY, SELL, Candidate, Leg
from conftest import IST, NOW, later, make_bars, make_contracts, make_raw_chain

EXPIRY = date(2026, 11, 24)
KEY = "CE24000"


def leg(side=BUY, strike=24000.0, option_type="CE", expiry=EXPIRY, key=KEY, ratio=1, iv=0.14):
    return Leg(
        instrument_key=key,
        expiry=expiry,
        strike=strike,
        option_type=option_type,
        side=side,
        ratio=ratio,
        lot_size=65,
        bid=99.0,
        ask=100.0,
        mid=99.5,
        iv=iv,
        delta=0.5 * side,
        gamma=0.0005,
        theta=-5.0,
        vega=10.0,
        oi=100000.0,
        volume=100000.0,
        T=12 / 365,
        forward=24050.0,
    )


def candidate(legs=None, profile="directional_buy", strategy="long_call"):
    return Candidate("NIFTY", strategy, profile, "bullish", legs or (leg(),))


def structure(**overrides):
    values = {
        "entry_debit": 6500.0,
        "max_loss": 6500.0,
        "max_profit": 50000.0,
        "bounded": True,
        "margin": 6500.0,
        "delta_notional": 780000.0,
        "net_vega": 650.0,
        "gamma_1pct": 900.0,
    }
    return SimpleNamespace(**(values | overrides))


def idea(lots=1, target=3250.0, stop=-1625.0, debit=6500.0, cand=None, exit_dte=1):
    return SimpleNamespace(
        tradable=True,
        candidate=cand or candidate(),
        structure=SimpleNamespace(entry_debit=debit),
        plan=ExitPlan(target, stop, exit_dte, abs(stop) * 1.25),
        risk=SimpleNamespace(lots=lots, planned_loss=abs(stop) * 1.25 * lots, margin=debit * lots),
        card=SimpleNamespace(composite=70.0),
        final_score=70.0,
        ctx_spot=24000.0,
    )


def snapshot(key=KEY, bid=99.0, ask=100.0, spot=24000.0, bid_qty=650.0, ask_qty=650.0):
    return {
        "NIFTY": pd.DataFrame(
            [
                {
                    "instrument_key": key,
                    "bid": bid,
                    "ask": ask,
                    "mid": (bid + ask) / 2,
                    "bid_qty": bid_qty,
                    "ask_qty": ask_qty,
                    "delta": 0.5,
                    "gamma": 0.0005,
                    "vega": 10.0,
                    "spot": spot,
                }
            ]
        )
    }


@pytest.fixture
def broker(tmp_path, risk_config, cost_model):
    return PaperBroker(Ledger(tmp_path / "paper.sqlite"), risk_config, cost_model)


def one(broker, *statuses):
    positions = broker.ledger.positions(statuses)
    assert len(positions) == 1
    return positions[0]


def open_position(broker, **kwargs):
    broker.submit(idea(**kwargs), NOW)
    broker.step(later(5), snapshot())
    return one(broker, OPEN)


def test_submit_does_not_fill_until_next_snapshot(broker):
    broker.submit(idea(), NOW)
    assert one(broker, PENDING).legs[0].entry_price is None


def test_entry_crosses_spread_and_pays_slippage(broker, cost_model):
    position = open_position(broker)
    assert position.legs[0].entry_price == pytest.approx(100.05)
    assert position.entry_costs == pytest.approx(
        cost_model.order(True, 100.05, 65, later(5).date())
    )


def test_short_entry_sells_at_bid_less_slippage(broker):
    sold = candidate((leg(side=SELL),), profile="short_premium", strategy="short_x")
    broker.submit(idea(cand=sold, debit=-6435.0), NOW)
    broker.step(later(5), snapshot())
    assert one(broker, OPEN).legs[0].entry_price == pytest.approx(98.95)


def test_stale_idea_is_cancelled(broker):
    broker.submit(idea(), NOW)
    report = broker.step(later(11), snapshot())
    assert report.cancelled and one(broker, CANCELLED).exit_reason == "idea expired before a fill"


def test_entry_cancelled_without_depth(broker):
    broker.submit(idea(), NOW)
    broker.step(later(5), snapshot(ask_qty=10.0))
    assert "insufficient depth" in one(broker, CANCELLED).exit_reason


def test_entry_cancelled_on_one_sided_quote(broker):
    broker.submit(idea(), NOW)
    broker.step(later(5), snapshot(ask=0.0))
    assert "one-sided" in one(broker, CANCELLED).exit_reason


def test_entry_cancelled_when_price_runs_away(broker):
    broker.submit(idea(), NOW)
    broker.step(later(5), snapshot(bid=129.0, ask=130.0))
    assert "price moved" in one(broker, CANCELLED).exit_reason


def test_entry_cancelled_when_contract_is_missing(broker):
    broker.submit(idea(), NOW)
    broker.step(later(5), snapshot(key="OTHER"))
    assert "no quote" in one(broker, CANCELLED).exit_reason


def test_target_exit_fills_on_following_snapshot_with_exact_pnl(broker, cost_model):
    open_position(broker)
    report = broker.step(later(10), snapshot(bid=150.0, ask=152.0))
    assert report.exits_triggered and one(broker, EXITING).exit_reason == "target"
    broker.step(later(15), snapshot(bid=150.0, ask=152.0))
    position = one(broker, CLOSED)
    day = later(5).date()
    gross = (149.95 - 100.05) * 65
    costs = cost_model.order(True, 100.05, 65, day) + cost_model.order(False, 149.95, 65, day)
    assert position.realized_pnl == pytest.approx(gross - costs)
    assert broker.ledger.realized_total() == pytest.approx(gross - costs)


def test_stop_exit(broker):
    open_position(broker)
    broker.step(later(10), snapshot(bid=70.0, ask=71.0))
    assert one(broker, EXITING).exit_reason == "stop"


def test_stop_does_not_trigger_on_missing_quotes(broker):
    open_position(broker)
    report = broker.step(later(10), snapshot(key="OTHER"))
    position = one(broker, OPEN)
    assert position.stale and not report.exits_triggered


def test_exit_waits_for_quotes_on_every_leg(broker):
    open_position(broker)
    broker.step(later(10), snapshot(bid=70.0, ask=71.0))
    broker.step(later(15), snapshot(key="OTHER"))
    assert one(broker, EXITING)
    broker.step(later(20), snapshot(bid=70.0, ask=71.0))
    assert one(broker, CLOSED).legs[0].exit_price == pytest.approx(69.95)


def test_time_exit_starts_at_three_pm_the_day_before_expiry(broker):
    open_position(broker)
    before = datetime(2026, 11, 23, 14, 55, tzinfo=IST)
    assert not broker.step(before, snapshot()).exits_triggered
    at_time = datetime(2026, 11, 23, 15, 0, tzinfo=IST)
    broker.step(at_time, snapshot())
    assert one(broker, EXITING).exit_reason == "time"


def test_settlement_uses_last_spot_before_expiry_and_charges_exercise_stt(broker, cost_model):
    open_position(broker, exit_dte=0, target=1e9, stop=-1e9)
    # Time exit is due at 15:00 on expiry, so settle a position whose quotes vanish before then.
    close_time = datetime(2026, 11, 24, 14, 55, tzinfo=IST)
    broker.step(close_time, snapshot(spot=24100.0))
    after = datetime(2026, 11, 24, 15, 35, tzinfo=IST)
    report = broker.step(after, {})
    position = one(broker, CLOSED)
    assert report.closed
    assert position.exit_reason == "expiry_settlement"
    assert position.settlement_estimated
    assert position.legs[0].exit_price == pytest.approx(100.0)
    exercise = cost_model.exercise(100.0, 65, EXPIRY)
    entry = cost_model.order(True, 100.05, 65, later(5).date())
    assert position.realized_pnl == pytest.approx((100 - 100.05) * 65 - entry - exercise)


def test_settlement_ignores_prices_after_expiry(broker):
    open_position(broker, exit_dte=0, target=1e9, stop=-1e9)
    broker.step(datetime(2026, 11, 24, 14, 55, tzinfo=IST), snapshot(spot=24100.0))
    next_morning = datetime(2026, 11, 25, 9, 20, tzinfo=IST)
    broker.step(next_morning, snapshot(key="OTHER", spot=30000.0))
    assert one(broker, CLOSED).legs[0].exit_price == pytest.approx(100.0)


def test_otm_expiry_has_no_exercise_charge(broker, cost_model):
    open_position(broker, exit_dte=0, target=1e9, stop=-1e9)
    broker.step(datetime(2026, 11, 24, 14, 55, tzinfo=IST), snapshot(spot=23000.0))
    broker.step(datetime(2026, 11, 24, 15, 35, tzinfo=IST), {})
    position = one(broker, CLOSED)
    assert position.exit_costs == 0
    assert position.realized_pnl == pytest.approx(-100.05 * 65 - position.entry_costs)


def test_calendar_settles_near_leg_and_sells_far_leg(broker):
    far_expiry = date(2026, 12, 29)
    calendar = candidate(
        (leg(side=SELL), leg(side=BUY, expiry=far_expiry, key="FAR")),
        profile="calendar",
        strategy="call_calendar",
    )
    broker.submit(idea(cand=calendar, debit=104.0, exit_dte=0, target=1e9, stop=-1e9), NOW)
    book = pd.concat([snapshot()["NIFTY"], snapshot(key="FAR", bid=99.5, ask=100.5)["NIFTY"]])
    broker.step(later(5), {"NIFTY": book})
    broker.step(datetime(2026, 11, 24, 14, 55, tzinfo=IST), {"NIFTY": book.assign(spot=24050.0)})
    after = datetime(2026, 11, 24, 15, 35, tzinfo=IST)
    broker.step(after, {})
    assert one(broker, EXITING)
    broker.step(after + timedelta(minutes=5), snapshot(key="FAR", bid=200.0, ask=201.0))
    position = one(broker, CLOSED)
    assert [leg_.exit_price for leg_ in position.legs] == pytest.approx([50.0, 199.95])


def test_daily_loss_halts_new_entries_and_exits_open_positions(broker, risk_config):
    open_position(broker, lots=4, target=1e9, stop=-1e9)
    report = broker.step(later(10), snapshot(bid=40.0, ask=41.0))
    assert report.halted
    assert one(broker, EXITING).exit_reason == "daily_loss_halt"
    broker.submit(idea(), later(11))
    broker.step(later(15), snapshot(bid=40.0, ask=41.0))
    assert broker.portfolio_state(later(16)).halted
    assert broker.ledger.positions((CANCELLED,))[0].exit_reason == "daily loss limit reached"
    next_day = later(5) + timedelta(days=1)
    assert not broker.portfolio_state(next_day).halted


def test_day_pnl_is_measured_from_the_previous_session(broker):
    open_position(broker, target=1e9, stop=-1e9)
    broker.step(later(10), snapshot(bid=89.0, ask=90.0))
    next_day = NOW + timedelta(days=1)
    broker.step(next_day, snapshot(bid=89.0, ask=90.0))
    assert broker.portfolio_state(next_day).day_pnl == pytest.approx(0.0)


def test_portfolio_state_tracks_risk_and_duplicates(broker):
    broker.submit(idea(lots=2), NOW)
    state = broker.portfolio_state(NOW)
    assert state.position_count == 1
    assert state.open_risk == pytest.approx(1625 * 1.25 * 2)
    assert state.risk_by_symbol["NIFTY"] == state.open_risk
    assert position_key(candidate()) in state.open_keys
    assert state.delta_notional == pytest.approx(0.5 * 130 * 24000)


def test_positions_survive_a_restart(tmp_path, risk_config, cost_model):
    path = tmp_path / "paper.sqlite"
    first = PaperBroker(Ledger(path), risk_config, cost_model)
    first.submit(idea(), NOW)
    first.step(later(5), snapshot())
    second = PaperBroker(Ledger(path), risk_config, cost_model)
    position = one(second, OPEN)
    assert position.legs[0].entry_price == pytest.approx(100.05)


def test_exit_plan_is_capped_by_structure(risk_config):
    spread = structure(entry_debit=5000.0, max_loss=2000.0, max_profit=1000.0)
    plan = make_exit_plan(candidate(profile="debit_spread"), spread, risk_config)
    assert plan.target_pnl == 1000.0
    assert plan.stop_pnl == -2000.0
    assert plan.planned_loss == 2000.0


def test_undefined_exit_plan_adds_gap_buffer(risk_config):
    short = structure(entry_debit=-4000.0, bounded=False)
    plan = make_exit_plan(candidate(profile="short_premium"), short, risk_config)
    assert plan.stop_pnl == -4000.0
    assert plan.planned_loss == 5000.0


def evaluate(risk_config, state=None, spec=None, plan=None, stress=None, cand=None):
    return evaluate_risk(
        cand or candidate(),
        spec or structure(margin=1000.0, delta_notional=10000.0, net_vega=10.0, gamma_1pct=10.0),
        plan or ExitPlan(1000.0, -800.0, 1, 1000.0),
        stress or StressResult(-1000.0, -5, 50),
        state or PortfolioState(capital=risk_config.capital),
        risk_config,
    )


def test_sizing_floors_to_per_trade_limit(risk_config):
    decision = evaluate(risk_config)
    assert decision.approved
    assert decision.lots == 5
    assert decision.planned_loss == 5000.0


def test_one_lot_above_limit_is_rejected(risk_config):
    decision = evaluate(risk_config, plan=ExitPlan(1.0, -1.0, 1, 5001.0))
    assert not decision.approved
    assert "per-trade loss limit" in decision.reasons[0]


def test_undefined_risk_uses_the_tighter_limit(risk_config):
    spec = structure(margin=1000.0, delta_notional=0.0, net_vega=0.0, gamma_1pct=0.0, bounded=False)
    allowed = risk_config.model_copy(update={"allow_unlimited_risk": True})
    decision = evaluate(allowed, spec=spec, plan=ExitPlan(1.0, -1.0, 1, 1000.0))
    assert decision.lots == 3


def test_undefined_risk_is_rejected_when_switched_off(risk_config):
    from algoverse.risk.limits import UNLIMITED_RISK_BLOCKED

    spec = structure(margin=1000.0, delta_notional=0.0, net_vega=0.0, gamma_1pct=0.0, bounded=False)
    decision = evaluate(risk_config, spec=spec, plan=ExitPlan(1.0, -1.0, 1, 1000.0))
    assert not decision.approved and decision.reasons == (UNLIMITED_RISK_BLOCKED,)


def test_stress_loss_limits_size(risk_config):
    decision = evaluate(risk_config, stress=StressResult(-60000.0, -5, 50))
    assert not decision.approved
    assert "stress loss limit (-5% spot, +50% IV)" in decision.reasons[0]


def test_open_risk_limit(risk_config):
    state = PortfolioState(capital=risk_config.capital, open_risk=23000.0)
    assert evaluate(risk_config, state=state).lots == 2


def test_underlying_risk_limit(risk_config):
    state = PortfolioState(capital=risk_config.capital, risk_by_symbol={"NIFTY": 14000.0})
    assert evaluate(risk_config, state=state).lots == 1


def test_margin_limit(risk_config):
    state = PortfolioState(capital=risk_config.capital, margin_used=249500.0)
    decision = evaluate(risk_config, state=state)
    assert decision.lots == 0 or decision.approved is False


def test_halt_duplicate_and_position_count_reject(risk_config):
    halted = PortfolioState(capital=risk_config.capital, halted=True)
    assert "daily loss" in evaluate(risk_config, state=halted).reasons[0]
    duplicate = PortfolioState(capital=risk_config.capital, open_keys={position_key(candidate())})
    assert "already open" in evaluate(risk_config, state=duplicate).reasons[0]
    full = PortfolioState(capital=risk_config.capital, position_count=5)
    assert "maximum open" in evaluate(risk_config, state=full).reasons[0]


def test_greek_limits_reduce_size_and_can_reject(risk_config):
    spec = structure(margin=1000.0, delta_notional=700000.0, net_vega=0.0, gamma_1pct=0.0)
    assert evaluate(risk_config, spec=spec).lots == 2
    vega = structure(margin=1000.0, delta_notional=0.0, net_vega=6000.0, gamma_1pct=0.0)
    decision = evaluate(risk_config, spec=vega)
    assert not decision.approved and decision.reasons == ("net vega limit",)


def test_offsetting_delta_is_allowed(risk_config):
    spec = structure(margin=1000.0, delta_notional=-700000.0, net_vega=0.0, gamma_1pct=0.0)
    state = PortfolioState(capital=risk_config.capital, delta_notional=1400000.0)
    # Five lots would overshoot to -2.1M, past the 1.5M limit on the other side.
    assert evaluate(risk_config, state=state, spec=spec).lots == 4


def test_stress_matches_repricing_by_hand(risk_config):
    deep = leg(side=SELL, strike=20000.0, option_type="CE")
    from_stress = stress_test(candidate((deep,)), risk_config.stress, 0.0)
    assert from_stress.spot_move_pct == 5
    expected = -(24050 * 1.05 - 20000 - 99.0) * 65
    assert from_stress.worst_pnl == pytest.approx(expected, rel=1e-3)


def build_deps(tmp_path, risk_config, scoring_config, cost_model):
    store = SnapshotStore(tmp_path)
    store.upsert_candles("NIFTY", make_bars())
    store.upsert_candles("INDIAVIX", make_bars(start=14.0, drift=0.0).assign(close=14.0))
    return CycleDeps(
        store=store,
        broker=PaperBroker(Ledger(tmp_path / "paper.sqlite"), risk_config, cost_model),
        scoring=scoring_config,
        risk=risk_config,
        costs=cost_model,
        events=EventCalendar(reviewed_through=date(2027, 1, 31)),
    )


def test_full_cycle_opens_position_on_next_snapshot_and_records_iv(
    tmp_path, risk_config, scoring_config, cost_model
):
    risk = risk_config.model_copy(deep=True)
    risk.paper.min_score = 0.0
    deps = build_deps(tmp_path, risk, scoring_config, cost_model)
    raw = make_raw_chain()
    contracts = make_contracts(raw)
    first = run_cycle(deps, {"NIFTY": raw}, contracts, NOW)
    assert len(first.submitted) == 1
    assert deps.broker.ledger.positions((OPEN,)) == []
    assert not deps.store.read_candles("iv_NIFTY").empty
    second_time = later(5)
    second = run_cycle(deps, {"NIFTY": make_raw_chain(now=second_time)}, contracts, second_time)
    assert len(second.report.opened) == 1
    assert len(deps.broker.ledger.positions((OPEN,))) == 1


def test_analysis_mode_changes_nothing(tmp_path, risk_config, scoring_config, cost_model):
    risk = risk_config.model_copy(deep=True)
    risk.paper.min_score = 0.0
    deps = build_deps(tmp_path, risk, scoring_config, cost_model)
    raw = make_raw_chain()
    result = run_cycle(deps, {"NIFTY": raw}, make_contracts(raw), NOW, trade=False)
    assert result.ideas and not result.submitted and result.report is None
    assert deps.broker.ledger.positions((PENDING, OPEN)) == []
    assert deps.store.read_candles("iv_NIFTY").empty


def test_min_score_gate_blocks_auto_trading(tmp_path, risk_config, scoring_config, cost_model):
    risk = risk_config.model_copy(deep=True)
    risk.paper.min_score = 101.0
    deps = build_deps(tmp_path, risk, scoring_config, cost_model)
    raw = make_raw_chain()
    assert run_cycle(deps, {"NIFTY": raw}, make_contracts(raw), NOW).submitted == []

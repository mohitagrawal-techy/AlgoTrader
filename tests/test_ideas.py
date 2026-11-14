from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
import pytest
from algoverse.evaluation.config import (
    Event,
    EventCalendar,
    Weight,
    load_event_calendar,
)
from algoverse.evaluation.context import build_context, completed_bars
from algoverse.evaluation.filters import filter_reasons
from algoverse.evaluation.params.base import (
    REGISTRY,
    ParamInput,
    by_bias,
    evaluate_param,
    param,
    scale,
)
from algoverse.evaluation.pipeline import generate_ideas
from algoverse.evaluation.scorer import Scorer, apply_preference
from algoverse.evaluation.structure import analyse, horizon_pnl
from algoverse.execution.cycle import prepare_chains
from algoverse.risk.costs import CostModel, CostRates, CostSchedule
from algoverse.risk.limits import PortfolioState
from algoverse.strategies.base import BUY, SELL
from algoverse.strategies.generate import generate_candidates
from conftest import CONFIG_DIR, EXPIRIES, NOW, make_bars, make_contracts, make_raw_chain

REVIEWED = EventCalendar(reviewed_through=date(2027, 1, 31))


@pytest.fixture
def chain(scoring_config):
    raw = make_raw_chain()
    return prepare_chains({"NIFTY": raw}, make_contracts(raw), NOW, scoring_config.risk_free_rate)[
        "NIFTY"
    ]


@pytest.fixture
def context(chain, scoring_config):
    return make_context(chain, scoring_config)


def make_context(chain, scoring_config, bars=None, events=REVIEWED, vix=None):
    vix_bars = make_bars(drift=0.0, start=14.0, seed=9) if vix is None else vix
    vix_bars = vix_bars.assign(close=np.linspace(11, 18, len(vix_bars)))
    return build_context(
        "NIFTY",
        NOW,
        chain,
        make_bars() if bars is None else bars,
        vix_bars,
        pd.Series(np.linspace(0.10, 0.20, 100)),
        events,
        scoring_config.risk_free_rate,
    )


@pytest.fixture
def candidates(chain, scoring_config):
    return {
        (c.strategy, c.front_expiry): c
        for c in generate_candidates("NIFTY", chain, scoring_config.generation)
    }


def pick(candidates, strategy, expiry=EXPIRIES[1]):
    return candidates[(strategy, expiry)]


def metrics(candidate, context, risk_config, cost_model, weight=0.5):
    return analyse(candidate, context, risk_config, cost_model, weight)


def test_cost_model_matches_hand_calculation(cost_model):
    # Buy: 20 + 2.27695 + 0.0065 + 18% of 22.28345 + 0.195 stamp.
    assert cost_model.order(True, 100.0, 65, date(2026, 11, 12)) == pytest.approx(26.489471)
    # Sell: 20 + 3.415425 + 0.00975 + 18% of 23.425175 + 14.625 STT.
    assert cost_model.order(False, 150.0, 65, date(2026, 11, 12)) == pytest.approx(42.2667065)


def test_costs_use_rates_in_force_on_trade_date(cost_model):
    before = cost_model.order(False, 100.0, 100, date(2026, 3, 31))
    after = cost_model.order(False, 100.0, 100, date(2026, 4, 1))
    assert after - before == pytest.approx(10000 * 0.05 / 100)
    assert cost_model.exercise(100.0, 65, date(2026, 11, 12)) == pytest.approx(9.75)


def test_costs_refuse_dates_without_a_schedule():
    rates = CostRates(
        effective_from=date(2026, 1, 1),
        brokerage_per_order=20,
        stt_sell_pct=0,
        stt_exercise_pct=0,
        exchange_pct=0,
        sebi_pct=0,
        stamp_buy_pct=0,
        gst_pct=0,
    )
    with pytest.raises(ValueError):
        CostModel(CostSchedule(schedules=[rates])).rates(date(2025, 1, 1))


def test_every_strategy_is_generated(candidates):
    assert {key[0] for key in candidates} == {
        "long_call",
        "long_put",
        "bull_call_debit",
        "bear_put_debit",
        "bull_put_credit",
        "bear_call_credit",
        "iron_condor",
        "call_butterfly",
        "short_straddle",
        "short_strangle",
        "long_straddle",
        "long_strangle",
        "call_calendar",
    }


def test_iron_condor_shape(candidates):
    legs = pick(candidates, "iron_condor").legs
    assert [leg.side for leg in legs] == [BUY, SELL, SELL, BUY]
    assert [leg.option_type for leg in legs] == ["PE", "PE", "CE", "CE"]
    strikes = [leg.strike for leg in legs]
    assert strikes == sorted(strikes)


def test_butterfly_is_symmetric_with_double_body(candidates):
    low, body, high = pick(candidates, "call_butterfly").legs
    assert body.ratio == 2 and body.side == SELL
    assert high.strike - body.strike == body.strike - low.strike == 200


def test_calendar_uses_two_expiries_and_one_strike(candidates):
    near, far = pick(candidates, "call_calendar", EXPIRIES[0]).legs
    assert near.side == SELL and far.side == BUY
    assert near.strike == far.strike and near.expiry < far.expiry


def test_unquoted_contracts_are_never_selected(chain, scoring_config):
    damaged = chain.copy()
    damaged.loc[damaged["strike"] == 24000.0, "mid"] = np.nan
    for candidate in generate_candidates("NIFTY", damaged, scoring_config.generation):
        assert all(leg.strike != 24000.0 for leg in candidate.legs)


def test_expired_series_produce_no_candidates(chain, scoring_config):
    expired = chain[chain["expiry"] == EXPIRIES[0]].assign(T=-0.001)
    assert generate_candidates("NIFTY", expired, scoring_config.generation) == []


def test_long_call_metrics(candidates, context, risk_config, cost_model):
    candidate = pick(candidates, "long_call")
    leg = candidate.legs[0]
    result = metrics(candidate, context, risk_config, cost_model)
    assert result.entry_debit == pytest.approx(leg.ask * 65)
    assert result.max_loss == pytest.approx(leg.ask * 65, rel=1e-3)
    assert result.bounded
    assert result.breakevens[0] == pytest.approx(leg.strike + leg.ask, abs=0.5)
    assert result.margin == pytest.approx(result.entry_debit)


def test_debit_spread_hand_calculation(candidates, context, risk_config, cost_model):
    candidate = pick(candidates, "bull_call_debit")
    low, high = candidate.legs
    debit = low.ask - high.bid
    result = metrics(candidate, context, risk_config, cost_model)
    assert result.max_loss == pytest.approx(debit * 65, rel=1e-3)
    assert result.max_profit == pytest.approx((high.strike - low.strike - debit) * 65, rel=1e-3)
    assert result.breakevens[0] == pytest.approx(low.strike + debit, abs=0.5)


def test_credit_is_negative_debit(candidates, context, risk_config, cost_model):
    result = metrics(pick(candidates, "iron_condor"), context, risk_config, cost_model)
    assert result.entry_debit < 0
    assert result.bounded
    assert len(result.breakevens) == 2


def test_short_premium_is_marked_undefined(candidates, context, risk_config, cost_model):
    result = metrics(pick(candidates, "short_straddle"), context, risk_config, cost_model)
    assert not result.bounded
    assert result.max_profit == pytest.approx(-result.entry_debit, rel=1e-3)
    assert result.margin > 0


def test_calendar_loss_is_bounded(candidates, context, risk_config, cost_model):
    result = metrics(
        pick(candidates, "call_calendar", EXPIRIES[0]), context, risk_config, cost_model
    )
    assert result.bounded
    assert result.max_loss <= result.entry_debit * 1.01


def test_horizon_pnl_values_far_leg_with_time_remaining(candidates, context):
    candidate = pick(candidates, "call_calendar", EXPIRIES[0])
    near, far = candidate.legs
    pnl = horizon_pnl(candidate, np.array([near.strike]), context.r)[0]
    assert pnl > -(far.ask - near.bid) * 65


def test_pop_of_at_the_money_straddle_is_plausible(candidates, context, risk_config, cost_model):
    long_side = metrics(
        pick(candidates, "long_straddle"), context, risk_config, cost_model, weight=1.0
    )
    short_side = metrics(
        pick(candidates, "short_straddle"), context, risk_config, cost_model, weight=1.0
    )
    assert 0.2 < long_side.pop < 0.5
    assert short_side.pop == pytest.approx(1 - long_side.pop, abs=0.05)


def test_ev_of_fairly_priced_option_is_near_minus_costs(
    candidates, chain, scoring_config, risk_config, cost_model
):
    context = make_context(chain, scoring_config)
    result = metrics(pick(candidates, "long_call"), context, risk_config, cost_model, weight=1.0)
    half_spread = 0.3 * 65
    assert result.ev == pytest.approx(-half_spread, abs=40)
    assert result.net_ev < result.ev


def test_context_ignores_todays_unfinished_bar():
    bars = make_bars(end=date(2026, 11, 12), count=10)
    assert completed_bars(bars, date(2026, 11, 12))["ts"].iloc[-1].date() == date(2026, 11, 11)


def test_context_is_unchanged_by_a_future_bar(chain, scoring_config):
    bars = make_bars()
    future = bars.tail(1).assign(ts=pd.Timestamp("2026-11-13", tz="Asia/Kolkata"), close=99999.0)
    extended = pd.concat([bars, future], ignore_index=True)
    first = make_context(chain, scoring_config, bars=bars)
    # A bar dated tomorrow would be a data error, but today's bar must not leak in either.
    today = future.assign(ts=pd.Timestamp("2026-11-12", tz="Asia/Kolkata"))
    second = make_context(chain, scoring_config, bars=pd.concat([bars, today], ignore_index=True))
    assert first.regime == second.regime
    assert first.realised_vol == second.realised_vol
    assert len(extended) == len(bars) + 1


def test_iv_uses_own_history_then_labels_the_proxy(chain, scoring_config):
    own = make_context(chain, scoring_config)
    assert own.iv.source == "own_snapshots"
    proxy = build_context(
        "NIFTY",
        NOW,
        chain,
        make_bars(),
        make_bars(),
        pd.Series(dtype=float),
        REVIEWED,
        scoring_config.risk_free_rate,
    )
    assert proxy.iv.source == "india_vix_proxy"
    assert proxy.iv.ranking is not None


def test_scale_and_bias():
    assert scale(5, 0, 10) == 50
    assert scale(-1, 0, 10) == 0
    assert scale(11, 0, 10) == 100
    assert by_bias(80, "bullish") == 80
    assert by_bias(80, "bearish") == 20
    assert by_bias(50, "neutral") == 100


def test_preference_directions():
    assert apply_preference(80, "high") == 80
    assert apply_preference(80, "low") == 20
    assert apply_preference(80, "mid") == 40


def test_every_registered_parameter_returns_a_valid_score(
    candidates, context, risk_config, cost_model
):
    for candidate in candidates.values():
        data = ParamInput(candidate, metrics(candidate, context, risk_config, cost_model), context)
        for name in REGISTRY:
            result = evaluate_param(name, data)
            assert result.score is None or 0 <= result.score <= 100, name


def test_scorer_hand_calculation(candidates, context, risk_config, cost_model):
    @param("test_a", "test")
    def _a(data):
        return 1.0, 80.0

    @param("test_b", "test")
    def _b(data):
        return 1.0, 20.0

    @param("test_missing", "test")
    def _missing(data):
        return None

    try:
        candidate = pick(candidates, "long_call")
        scorer = Scorer(
            {
                candidate.profile: {
                    "test_a": Weight(w=3),
                    "test_b": Weight(w=1, prefer="low"),
                    "test_missing": Weight(w=4),
                }
            }
        )
        structure = metrics(candidate, context, risk_config, cost_model)
        card = scorer.score(ParamInput(candidate, structure, context))
        assert card.composite == pytest.approx(80.0)
        assert card.coverage == pytest.approx(0.5)
    finally:
        for name in ("test_a", "test_b", "test_missing"):
            REGISTRY.pop(name)


def test_scorer_rejects_unknown_parameters():
    with pytest.raises(ValueError):
        Scorer({"x": {"nope": Weight(w=1)}})


def test_shipped_configs_are_consistent(scoring_config, risk_config):
    Scorer(scoring_config.profiles)
    assert set(scoring_config.profiles) == set(risk_config.exits)
    assert load_event_calendar(CONFIG_DIR).reviewed_through is None


def test_high_iv_rank_helps_sellers_and_hurts_buyers(
    chain, scoring_config, risk_config, cost_model
):
    def composite(strategy, ivs):
        context = make_context(chain, scoring_config)
        context = build_context(
            "NIFTY",
            NOW,
            chain,
            make_bars(),
            make_bars(),
            pd.Series(ivs),
            REVIEWED,
            context.r,
        )
        all_candidates = generate_candidates("NIFTY", chain, scoring_config.generation)
        candidate = next(
            c for c in all_candidates if c.strategy == strategy and c.front_expiry == EXPIRIES[1]
        )
        structure = metrics(candidate, context, risk_config, cost_model)
        return Scorer(scoring_config.profiles).score(ParamInput(candidate, structure, context))

    cheap_history = np.linspace(0.05, 0.14, 100)
    rich_history = np.linspace(0.14, 0.30, 100)
    assert (
        composite("short_strangle", cheap_history).composite
        > composite("short_strangle", rich_history).composite
    )
    assert (
        composite("long_straddle", cheap_history).composite
        < composite("long_straddle", rich_history).composite
    )


def reasons_for(candidate, context, scoring_config, risk_config, cost_model):
    structure = metrics(candidate, context, risk_config, cost_model)
    return filter_reasons(candidate, structure, context, scoring_config.filters, risk_config)


def test_liquid_defined_risk_passes_filters(
    candidates, context, scoring_config, risk_config, cost_model
):
    assert (
        reasons_for(
            pick(candidates, "iron_condor"), context, scoring_config, risk_config, cost_model
        )
        == []
    )


def test_wide_spread_low_oi_and_volume_fail(
    candidates, context, scoring_config, risk_config, cost_model
):
    candidate = pick(candidates, "long_call")
    leg = replace(candidate.legs[0], bid=50.0, ask=100.0, mid=75.0, oi=10.0, volume=1.0)
    reasons = reasons_for(
        replace(candidate, legs=(leg,)), context, scoring_config, risk_config, cost_model
    )
    assert any("spread" in r for r in reasons)
    assert any("open interest" in r for r in reasons)
    assert any("volume" in r for r in reasons)


def test_cheap_wing_passes_on_absolute_spread(
    candidates, context, scoring_config, risk_config, cost_model
):
    candidate = pick(candidates, "long_call")
    leg = replace(candidate.legs[0], bid=2.0, ask=2.8, mid=2.4)
    reasons = reasons_for(
        replace(candidate, legs=(leg,)), context, scoring_config, risk_config, cost_model
    )
    assert not any("spread" in r for r in reasons)


def test_expiry_inside_exit_window_fails(
    candidates, context, scoring_config, risk_config, cost_model
):
    candidate = pick(candidates, "long_call", EXPIRIES[0])
    near = replace(context, now=NOW.replace(day=16))
    structure = metrics(candidate, near, risk_config, cost_model)
    reasons = filter_reasons(candidate, structure, near, scoring_config.filters, risk_config)
    assert any("inside the" in r for r in reasons)


def test_short_premium_is_blocked_when_calendar_is_unreviewed(
    chain, candidates, scoring_config, risk_config, cost_model
):
    context = make_context(chain, scoring_config, events=EventCalendar())
    reasons = reasons_for(
        pick(candidates, "short_strangle"), context, scoring_config, risk_config, cost_model
    )
    assert any("not reviewed" in r for r in reasons)
    defined = reasons_for(
        pick(candidates, "iron_condor"), context, scoring_config, risk_config, cost_model
    )
    assert defined == []


def test_short_premium_is_blocked_by_high_impact_event(
    chain, candidates, scoring_config, risk_config, cost_model
):
    events = EventCalendar(
        reviewed_through=date(2027, 1, 31),
        events=[Event(date=date(2026, 11, 20), name="RBI policy", severity="high")],
    )
    context = make_context(chain, scoring_config, events=events)
    reasons = reasons_for(
        pick(candidates, "short_strangle"), context, scoring_config, risk_config, cost_model
    )
    assert "high-impact event: RBI policy" in reasons
    unaffected = reasons_for(
        pick(candidates, "short_strangle", EXPIRIES[0]),
        context,
        scoring_config,
        risk_config,
        cost_model,
    )
    assert unaffected == []


def test_event_outside_scope_is_ignored():
    calendar = EventCalendar(
        events=[Event(date=date(2026, 11, 20), name="Earnings", severity="high", symbols=["TCS"])]
    )
    assert calendar.in_window("NIFTY", date(2026, 11, 12), date(2026, 11, 24)) == []
    assert len(calendar.in_window("TCS", date(2026, 11, 12), date(2026, 11, 24))) == 1


def test_pipeline_ranks_tradable_first_with_explanations(
    chain, context, scoring_config, risk_config, cost_model
):
    ideas = generate_ideas(
        context, chain, scoring_config, risk_config, cost_model, PortfolioState(capital=500000)
    )
    assert ideas
    flags = [idea.tradable for idea in ideas]
    assert flags == sorted(flags, reverse=True)
    tradable = [idea for idea in ideas if idea.tradable]
    assert tradable, "expected at least one tradable idea on a liquid chain"
    scores = [idea.card.composite for idea in tradable]
    assert scores == sorted(scores, reverse=True)
    for idea in ideas:
        assert idea.tradable or idea.rejections or idea.risk.reasons
        assert idea.card.parts


def test_one_unquoted_expiry_does_not_block_other_expiries(
    chain, candidates, scoring_config, risk_config, cost_model
):
    damaged = chain.copy()
    far = damaged["expiry"] == EXPIRIES[2]
    damaged.loc[far, "forward_implied"] = False
    context = make_context(damaged, scoring_config)
    near = reasons_for(
        pick(candidates, "iron_condor"), context, scoring_config, risk_config, cost_model
    )
    blocked = reasons_for(
        pick(candidates, "iron_condor", EXPIRIES[2]),
        context,
        scoring_config,
        risk_config,
        cost_model,
    )
    assert near == []
    assert any("could not be implied" in reason for reason in blocked)


def test_hedged_margin_counts_only_the_side_at_risk(candidates, context, risk_config, cost_model):
    one_short = 65 * context.spot * 0.02
    for strategy in ("iron_condor", "call_butterfly", "bear_call_credit", "bull_put_credit"):
        result = metrics(pick(candidates, strategy), context, risk_config, cost_model)
        assert result.margin == pytest.approx(max(result.max_loss * 1.2, one_short)), strategy


def test_debit_spread_margin_follows_its_small_maximum_loss(
    candidates, context, risk_config, cost_model
):
    for strategy in ("bull_call_debit", "bear_put_debit"):
        result = metrics(pick(candidates, strategy), context, risk_config, cost_model)
        assert result.margin == pytest.approx(result.max_loss * 1.2), strategy


def test_unlimited_risk_ideas_are_rejected_unless_allowed(
    chain, context, scoring_config, risk_config, cost_model
):
    from algoverse.risk.limits import UNLIMITED_RISK_BLOCKED

    def short_premium(config):
        ideas = generate_ideas(
            context, chain, scoring_config, config, cost_model, PortfolioState(capital=500000)
        )
        return [idea for idea in ideas if idea.candidate.strategy == "short_straddle"]

    blocked = short_premium(risk_config)
    assert blocked and not any(idea.tradable for idea in blocked)
    assert all(UNLIMITED_RISK_BLOCKED in idea.risk.reasons for idea in blocked if idea.risk)
    allowed = short_premium(risk_config.model_copy(update={"allow_unlimited_risk": True}))
    assert all(
        UNLIMITED_RISK_BLOCKED not in (idea.risk.reasons if idea.risk else ()) for idea in allowed
    )

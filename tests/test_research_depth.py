"""Regimes, trial-adjusted Sharpe, parameter search, dossiers, volatility sizing and breakers."""

import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from algoverse.ai.budget import AiLog
from algoverse.ai.client import LLMError, LLMResult
from algoverse.analytics.regimes import label_sessions, regime_breakdown
from algoverse.backtest.config import LabConfig
from algoverse.lab import dossier as dossiers
from algoverse.lab.evidence import (
    expected_max_sharpe,
    probabilistic_sharpe,
    significance,
    trial_spread,
)
from algoverse.lab.search import (
    FLOOR_SCORE,
    Param,
    apply_values,
    objective,
    run_search,
    search_space,
    searched_child,
    sensitivity,
)
from algoverse.lab.seeds import TEMPLATES
from algoverse.lab.spec import (
    Budget,
    StrategySpec,
    breaker_pause,
    budget_key,
    signature,
    volatility_scale,
)
from algoverse.lab.stages import PAPER_TRADE, RESEARCH, grade

# ---- Trial-adjusted Sharpe ------------------------------------------------------------------


def _returns(mean, sd, count=250, seed=1):
    return list(np.random.default_rng(seed).normal(mean, sd, count))


def test_probabilistic_sharpe_tracks_the_sign_of_the_edge():
    assert probabilistic_sharpe(_returns(0.002, 0.01)) > 0.9
    assert probabilistic_sharpe(_returns(-0.002, 0.01)) < 0.1
    assert probabilistic_sharpe([0.01, 0.01, 0.01]) is None
    assert probabilistic_sharpe([0.01]) is None


def test_more_trials_raise_the_luck_benchmark():
    assert expected_max_sharpe(1, 0.01) == 0.0
    assert 0 < expected_max_sharpe(10, 0.01) < expected_max_sharpe(1000, 0.01)


def test_deflated_sharpe_is_stricter_than_probabilistic():
    pnl = [r * 100_000 for r in _returns(0.001, 0.01)]
    plain = significance(pnl, 100_000, 1, None)
    assert plain["dsr"] == pytest.approx(plain["psr"])
    deflated = significance(pnl, 100_000, 500, trial_spread([-2, -1, 0, 1, 2, 0.5]))
    assert deflated["dsr"] < deflated["psr"]
    assert deflated["luck_sharpe"] > 0


def test_trial_spread_needs_two_values():
    assert trial_spread([1.0]) is None
    assert trial_spread([1.0, None, 3.0]) == pytest.approx(
        np.var([1 / 252**0.5, 3 / 252**0.5], ddof=1)
    )


CONFIG = LabConfig(stage_folds=4, stage_min_positive_share=0.6, stage_max_drawdown_pct=15)


def _graded_result(test_daily):
    days = [str(date(2026, 1, 1) + timedelta(days=i)) for i in range(len(test_daily))]
    return {
        "verdict": "held_up",
        "train": {"trades": 40, "net_pnl": 1000.0},
        "test": {"trades": 30, "net_pnl": sum(test_daily), "max_drawdown_pct": 3.0},
        "test_2x": {"trades": 30, "net_pnl": 100.0},
        "evidence": {"floor": 5.0},
        "detail": {
            "train": {"daily": {}, "trades": []},
            "test": {
                "daily": dict(zip(days, test_daily, strict=True)),
                "trades": [
                    {"closed": d, "net_pnl": p} for d, p in zip(days, test_daily, strict=True) if p
                ],
            },
        },
    }


def test_many_trials_hold_a_strategy_back_in_research():
    daily = [float(v) for v in np.random.default_rng(3).normal(150, 900, 120)]
    result = _graded_result(daily)
    alone = grade(result, False, False, CONFIG, 100_000, {"count": 1, "variance": None})
    crowded = grade(result, False, False, CONFIG, 100_000, {"count": 2000, "variance": 0.01})
    deflated = next(c for c in crowded["checks"] if c["key"] == "deflated")
    assert not deflated["passed"]
    assert crowded["stage"] == RESEARCH
    assert crowded["significance"]["dsr"] < alone["significance"]["dsr"]


def test_a_lone_peak_from_a_search_fails_the_robust_check():
    daily = [400.0 if i % 3 else -100.0 for i in range(60)]
    result = _graded_result(daily)
    robust = grade(
        result, False, False, CONFIG, 100_000, None, {"robust": True, "neighbour_share": 0.8}
    )
    assert robust["stage"] == PAPER_TRADE
    lone = grade(
        result, False, False, CONFIG, 100_000, None, {"robust": False, "neighbour_share": 0.2}
    )
    assert lone["stage"] == RESEARCH
    assert "nearby settings" in lone["reason"]


# ---- Regimes --------------------------------------------------------------------------------


def _bars(closes):
    days = pd.bdate_range("2024-01-01", periods=len(closes))
    close = pd.Series(closes, dtype=float)
    return pd.DataFrame(
        {
            "ts": days.tz_localize("Asia/Kolkata"),
            "open": close.shift(1).fillna(close),
            "high": close * 1.004,
            "low": close * 0.996,
            "close": close,
        }
    )


def test_a_steady_rise_is_a_strong_bull_and_labels_use_the_previous_close():
    closes = [100 * 1.004**i for i in range(200)]
    labels = label_sessions(_bars(closes))
    assert labels["trend"].iloc[-1] == "strong_bull"
    assert labels["trend"].iloc[0] is None
    # Labels are shifted a session: the first session can know nothing.
    assert labels.iloc[0].isna().all()


def test_a_jump_past_the_range_is_a_breakout_on_the_next_session():
    closes = [100 + (i % 2) * 0.2 for i in range(120)] + [110, 110]
    labels = label_sessions(_bars(closes))
    assert labels["move"].iloc[-2] != "breakout"
    assert labels["move"].iloc[-1] == "breakout"
    assert labels["trend"].iloc[100] == "sideways"


def test_volatility_rank_marks_calm_and_stormy_sessions():
    rng = np.random.default_rng(5)
    moves = np.concatenate([rng.normal(0, 0.003, 150), rng.normal(0, 0.03, 60)])
    labels = label_sessions(_bars(100 * np.exp(np.cumsum(moves))))
    assert labels["volatility"].iloc[-1] == "high_vol"


def test_breakdown_says_which_regimes_suit_the_strategy():
    frame = pd.DataFrame(
        {
            "trend": ["strong_bull"] * 6 + ["strong_bear"] * 6,
            "volatility": ["low_vol"] * 12,
            "move": ["no_signal"] * 12,
        },
        index=[str(date(2026, 1, 1) + timedelta(days=i)) for i in range(12)],
    )
    trades = [
        {"symbol": "NIFTY", "opened": day, "net_pnl": 100.0 if i < 6 else -50.0}
        for i, day in enumerate(frame.index)
    ] + [{"symbol": "OTHER", "opened": "2026-01-01", "net_pnl": 5.0}]
    found = regime_breakdown(trades, {"NIFTY": frame}, list(frame.index), 5)
    assert found["suits"] == ["strong_bull", "low_vol"]
    assert found["avoid"] == ["strong_bear"]
    assert found["unlabelled_trades"] == 1
    bull = next(row for row in found["rows"] if row["key"] == "strong_bull")
    assert bull["trades"] == 6 and bull["sessions"] == 6 and bull["win_rate"] == 1.0


# ---- Volatility sizing and circuit breakers ---------------------------------------------------


def test_new_budget_settings_keep_old_results_matching():
    old = Budget().model_dump(
        exclude={
            "vol_sizing",
            "vol_floor",
            "vol_cap",
            "pause_after_losses",
            "pause_drawdown_pct",
            "pause_sessions",
        }
    )
    assert budget_key(Budget()) == old
    assert budget_key(Budget(pause_after_losses=3))["pause_after_losses"] == 3


def test_volatility_scale_shrinks_trades_when_ranges_widen():
    calm = _bars([100 + (i % 2) for i in range(200)])
    assert volatility_scale(Budget(), calm) == 1.0
    assert volatility_scale(Budget(vol_sizing="atr"), calm) == pytest.approx(1.0, abs=0.05)
    wild = calm.copy()
    wild.loc[wild.index[-5:], "high"] = wild["close"].iloc[-5:] * 1.05
    scale = volatility_scale(Budget(vol_sizing="atr", vol_floor=0.4), wild)
    assert 0.4 <= scale < 0.9
    assert volatility_scale(Budget(vol_sizing="atr"), calm.iloc[:20]) == 1.0


def test_losing_streak_pauses_then_counts_afresh():
    budget = Budget(pause_after_losses=3, pause_sessions=2)
    day = date(2026, 3, 2)  # a Monday
    closed = [(day, -10.0), (day, -10.0), (day + timedelta(days=1), -10.0)]
    until, why = breaker_pause(budget, closed)
    assert until == date(2026, 3, 5) and "3 losing trades" in why
    # One more loss after the pause is not a new streak of three.
    assert breaker_pause(budget, [*closed, (date(2026, 3, 9), -5.0)])[0] == until
    assert breaker_pause(Budget(), closed) is None


def test_drawdown_breaker_measures_from_the_best_closed_pnl():
    budget = Budget(capital=100_000, pause_drawdown_pct=5, pause_sessions=1)
    day = date(2026, 3, 2)
    assert breaker_pause(budget, [(day, 3000.0), (day, -4000.0)]) is None
    until, why = breaker_pause(budget, [(day, 3000.0), (day, -4000.0), (day, -1500.0)])
    assert until == date(2026, 3, 3) and "5%" in why


# ---- Parameter search -------------------------------------------------------------------------


def _template(kind="delta"):
    return next(spec for spec in TEMPLATES if any(leg.select == kind for leg in spec.legs))


def test_search_space_covers_exits_strikes_and_timing():
    spec = _template()
    paths = {param.path for param in search_space(spec)}
    assert {"exits.target_frac", "exits.stop_frac", "entry.dte_max"} <= paths
    assert any(path.startswith("legs.") for path in paths)
    for param in search_space(spec):
        assert param.low <= param.clip(param.current) <= param.high


def test_param_grid_includes_the_current_value_and_respects_bounds():
    param = Param("exits.stop_frac", "Stop", "float", 0.2, 1.2, 0.05, 0.5)
    values = param.grid(5)
    assert 0.5 in values and min(values) == 0.2 and max(values) == 1.2
    assert Param("x", "x", "int", 1, 3, 1, 2).grid(5) == [1, 2, 3]


def test_apply_values_rejects_impossible_mixes():
    spec = _template()
    child = apply_values(spec, {"exits.stop_frac": 0.9})
    assert child is not None and child.exits.stop_frac == 0.9
    assert apply_values(spec, {"exits.stop_frac": spec.exits.stop_frac}) is not None
    assert apply_values(spec, {"entry.dte_max": 0}) is None


class FakeSearcher:
    """Scores a setting by how close its stop is to 0.8, on design and validation alike."""

    def __init__(self, workers=3, peak=0.8, spike=False):
        self.config = type("C", (), {"lab": LabConfig(workers=workers)})()
        self.peak, self.spike = peak, spike
        self.design_calls = 0
        self.runs = 0

    def _score(self, spec):
        distance = abs(spec.exits.stop_frac - self.peak)
        if self.spike:
            sharpe = 3.0 if distance < 1e-9 else -1.0
        else:
            sharpe = 2.0 - 4 * distance
        return {
            "trades": 30,
            "net_pnl": sharpe * 1000,
            "sharpe": sharpe,
            "win_rate": 0.5,
            "max_drawdown": 100.0,
        }

    def design_scores(self, specs):
        self.design_calls += len(specs)
        self.runs += len(specs)
        return [self._score(spec) for spec in specs]

    validate_scores = design_scores


@pytest.mark.parametrize("method", ["grid", "random", "bayesian"])
def test_every_method_finds_the_better_stop(method):
    spec = _template()
    stop = next(p for p in search_space(spec) if p.path == "exits.stop_frac")
    found = run_search(spec, [stop], method, 20, FakeSearcher())
    assert len(found["trials"]) <= 20
    best = found["ranked"][0]
    assert abs(best["values"]["exits.stop_frac"] - 0.8) <= 0.15
    assert found["ranked"] == sorted(found["ranked"], key=lambda r: r["score"], reverse=True)


def test_search_refuses_too_many_or_no_settings():
    spec = _template()
    with pytest.raises(ValueError):
        run_search(spec, [], "random", 10, FakeSearcher())
    with pytest.raises(ValueError):
        run_search(spec, search_space(spec)[:1] * 5, "random", 10, FakeSearcher())


def test_search_stops_when_asked():
    spec = _template()
    stop = next(p for p in search_space(spec) if p.path == "exits.stop_frac")
    found = run_search(
        spec, [stop], "random", 40, FakeSearcher(workers=2), should_stop=lambda: True
    )
    assert found["trials"] == []


def test_thin_samples_score_below_any_real_sharpe():
    assert objective({"trades": 2, "sharpe": 5.0}) < -4
    assert objective({"trades": 2, "sharpe": 5.0}) >= FLOOR_SCORE
    assert objective({"trades": 30, "sharpe": 0.4}) == 0.4


def test_sensitivity_tells_a_plateau_from_a_spike():
    spec = _template()
    params = [p for p in search_space(spec) if p.path in ("exits.stop_frac", "exits.target_frac")]
    center = {p.path: p.clip(p.current) for p in params}
    peak = center["exits.stop_frac"]
    plateau = sensitivity(spec, params, center, FakeSearcher(peak=peak), 0.6)
    assert plateau["robust"] and plateau["neighbour_share"] >= 0.6
    assert len(plateau["cells"]) == len(plateau["xs"]) * len(plateau["ys"])
    spike = sensitivity(spec, params, center, FakeSearcher(peak=peak, spike=True), 0.6)
    assert not spike["robust"]


def test_searched_child_is_a_new_strategy_pointing_at_its_root():
    spec = _template()
    params = [p for p in search_space(spec) if p.path == "exits.stop_frac"]
    child = searched_child(spec, {"exits.stop_frac": 0.9}, params)
    assert child.parent_id == spec.id and child.id != spec.id
    assert child.name.endswith(", searched") and "0.9" in child.step
    assert signature(child) != signature(spec)


# ---- Dossier ----------------------------------------------------------------------------------


def _tested(spec):
    result = _graded_result([400.0 if i % 3 else -100.0 for i in range(60)])
    result["windows"] = {"train_start": "2025-01-01", "test_start": "2026-01-01"}
    result["evaluated_at"] = "2026-11-14T10:00:00+05:30"
    return result


def test_dossier_has_fifteen_numbered_points_and_waits_for_the_ai():
    spec = _template()
    result = _tested(spec)
    built = dossiers.build(
        spec, result, grade(result, False, False, CONFIG, 100_000), None, None, None
    )
    sections = built["sections"]
    assert [s["number"] for s in sections] == list(range(1, 16))
    hypothesis = next(s for s in sections if s["title"] == "Hypothesis")
    assert hypothesis["source"] == "ai" and "Not written yet" in hypothesis["text"]
    text = dossiers.markdown(spec, built)
    assert text.startswith(f"# Research dossier: {spec.name}")
    assert "## 15. Stage and next steps" in text
    assert "| Trades |" in text


def test_dossier_facts_leave_out_the_ai_parts():
    spec = _template()
    result = _tested(spec)
    built = dossiers.build(
        spec, result, grade(result, False, False, CONFIG, 100_000), None, None, None
    )
    facts = dossiers.facts(built)
    assert "Hypothesis" not in facts and "Performance" in facts


class NoteWriter:
    model = "fake:1"

    def __init__(self, reply):
        self.reply = reply

    def complete(self, system, user, schema):
        assert "Use only numbers that appear in the facts" in system
        json.loads(user)
        return LLMResult(self.reply, 5, 5)


def test_ai_notes_fill_the_dossier_and_are_logged(tmp_path):
    spec = _template()
    result = _tested(spec)
    stage = grade(result, False, False, CONFIG, 100_000)
    built = dossiers.build(spec, result, stage, None, None, None)
    reply = json.dumps(
        {
            "hypothesis": "Range days decay premium.",
            "weaknesses": ["Short test."],
            "next_steps": ["Paper trade it."],
        }
    )
    log = AiLog(tmp_path / "ai.sqlite")
    notes = dossiers.write_notes(NoteWriter(reply), log, datetime(2026, 11, 14), spec, built)
    filled = dossiers.build(spec, result, stage, None, None, notes)
    hypothesis = next(s for s in filled["sections"] if s["title"] == "Hypothesis")
    assert hypothesis["text"] == "Range days decay premium."
    weak = next(s for s in filled["sections"] if s["title"] == "Weaknesses")
    assert "Short test." in weak["bullets"]
    with pytest.raises(LLMError):
        dossiers.write_notes(NoteWriter("not json"), log, datetime(2026, 11, 14), spec, built)


def test_untested_dossier_still_builds():
    spec = StrategySpec.model_validate(_template().model_dump())
    built = dossiers.build(spec, None, grade(None, False, False, CONFIG, 100_000), None, None, None)
    assert len(built["sections"]) == 15

from datetime import date, timedelta

import pandas as pd
from algoverse.backtest.config import LabConfig
from algoverse.backtest.report import daily_stats, exposure_stats, trade_stats
from algoverse.lab.stages import PAPER_TRADE, REJECT, RESEARCH, consecutive_folds, grade

CONFIG = LabConfig(stage_folds=4, stage_min_positive_share=0.6, stage_max_drawdown_pct=15)
START = date(2026, 1, 1)


def _detail(train_daily, test_daily):
    def window(values, offset):
        days = [str(START + timedelta(days=offset + i)) for i in range(len(values))]
        trades = [
            {"closed": day, "net_pnl": pnl} for day, pnl in zip(days, values, strict=True) if pnl
        ]
        return {"daily": dict(zip(days, values, strict=True)), "trades": trades}

    return {"train": window(train_daily, 0), "test": window(test_daily, len(train_daily))}


def _result(verdict="held_up", train=1000.0, test=800.0, doubled=300.0, floor=5.0, dd=4.0):
    return {
        "verdict": verdict,
        "train": {"trades": 40, "net_pnl": train},
        "test": {"trades": 20, "net_pnl": test, "max_drawdown_pct": dd},
        "test_2x": {"trades": 20, "net_pnl": doubled},
        "evidence": {"floor": floor},
        "detail": _detail([100, 50, 80, -20, 90, 60], [70, 40, -10, 90, 30, 50]),
    }


def test_untested_strategy_has_no_stage():
    assert grade(None, False, False, CONFIG, 100_000)["stage"] is None


def test_unlimited_risk_is_rejected_unless_allowed():
    assert grade(_result(), True, False, CONFIG, 100_000)["stage"] == REJECT
    assert grade(_result(), True, True, CONFIG, 100_000)["stage"] == PAPER_TRADE


def test_passing_every_check_is_paper_trade():
    graded = grade(_result(), False, False, CONFIG, 100_000)
    assert graded["stage"] == PAPER_TRADE
    assert all(check["passed"] for check in graded["checks"])
    assert len(graded["folds"]) == 4


def test_verdicts_map_to_reject_and_research():
    for verdict, stage in (
        ("failed", REJECT),
        ("fails_at_2x_slippage", REJECT),
        ("too_few_trades", RESEARCH),
        ("unproven", RESEARCH),
    ):
        assert grade(_result(verdict=verdict), False, False, CONFIG, 100_000)["stage"] == stage


def test_held_up_but_lost_in_training_stays_in_research():
    graded = grade(_result(train=-500.0), False, False, CONFIG, 100_000)
    assert graded["stage"] == RESEARCH
    assert "training" in graded["reason"]


def test_deep_drawdown_stays_in_research():
    assert grade(_result(dd=30.0), False, False, CONFIG, 100_000)["stage"] == RESEARCH


def test_drawdown_falls_back_to_rupees_over_budget():
    result = _result()
    result["test"] = {"trades": 20, "net_pnl": 800.0, "max_drawdown": 20_000.0}
    assert grade(result, False, False, CONFIG, 100_000)["stage"] == RESEARCH


def test_profit_from_one_period_fails_consistency():
    result = _result()
    result["detail"] = _detail([-10, -10, -10, -10, -10, -10], [-10, -10, -10, 500, -10, -10])
    graded = grade(result, False, False, CONFIG, 100_000)
    assert graded["stage"] == RESEARCH
    consistency = next(c for c in graded["checks"] if c["key"] == "consistency")
    assert not consistency["passed"]


def test_folds_cover_every_session_in_order():
    folds = consecutive_folds(_detail([1] * 5, [2] * 7), 3)
    assert sum(f["sessions"] for f in folds) == 12
    assert folds[0]["start"] < folds[1]["start"] < folds[2]["start"]
    assert sum(f["net_pnl"] for f in folds) == 5 + 14


def test_new_trade_and_daily_metrics():
    trades = pd.DataFrame(
        {
            "net_pnl": [100.0, 200.0, -50.0, 300.0, -100.0],
            "costs": [1.0] * 5,
            "holding_days": [0] * 5,
            "closed": pd.date_range("2026-01-01", periods=5),
        }
    )
    stats = trade_stats(trades, 0.2)
    assert stats["longest_winning_streak"] == 2
    assert stats["risk_reward"] == 200.0 / 75.0
    daily = pd.Series([100.0, -300.0, 500.0], index=[date(2026, 1, d) for d in (1, 2, 3)])
    summary = daily_stats(daily, 10_000)
    assert summary["total_return_pct"] == 3.0
    assert summary["recovery_factor"] == 300.0 / 300.0


def test_exposure_and_capital_use():
    sessions = [date(2026, 1, d) for d in range(1, 11)]
    trades = pd.DataFrame(
        {
            "opened": [date(2026, 1, 2), date(2026, 1, 3)],
            "closed": [date(2026, 1, 4), date(2026, 1, 3)],
            "margin": [20_000.0, 10_000.0],
        }
    )
    stats = exposure_stats(trades, sessions, 100_000)
    assert stats["exposure_pct"] == 30.0
    assert stats["peak_margin"] == 30_000.0
    assert stats["capital_use_pct"] == 30.0


def test_rupees_use_indian_grouping():
    from algoverse.lab.stages import rupees

    assert rupees(-652188.4) == "−₹6,52,188"
    assert rupees(1234) == "₹1,234"
    assert rupees(12345678) == "₹1,23,45,678"
    assert rupees(0) == "₹0"


def _spec(name, legs):
    from algoverse.lab.spec import StrategySpec

    return StrategySpec.model_validate(
        {"id": "x", "name": name, "legs": legs, "source": "user", "bias": "neutral"}
    )


def _leg(action, option_type, delta):
    return {"action": action, "option_type": option_type, "select": "delta", "value": delta}


def test_reverse_condor_named_iron_condor_is_flagged():
    from algoverse.lab.spec import structure_problems

    inverted = [
        _leg("sell", "PE", 0.08),
        _leg("buy", "PE", 0.2),
        _leg("buy", "CE", 0.2),
        _leg("sell", "CE", 0.08),
    ]
    proper = [
        _leg("buy", "PE", 0.08),
        _leg("sell", "PE", 0.2),
        _leg("sell", "CE", 0.2),
        _leg("buy", "CE", 0.08),
    ]
    assert (
        "reverse iron condor" in structure_problems(_spec("Short‑dated iron condor", inverted))[0]
    )
    assert structure_problems(_spec("Iron condor", proper)) == []
    assert structure_problems(_spec("Reverse iron condor", inverted)) == []


def test_spreads_and_straddles_must_match_their_names():
    from algoverse.lab.spec import structure_problems

    credit = [_leg("sell", "PE", 0.25), _leg("buy", "PE", 0.12)]
    assert structure_problems(_spec("Bull put credit spread", credit)) == []
    assert structure_problems(_spec("Put debit spread", credit))
    assert structure_problems(
        _spec("Long straddle", [_leg("sell", "CE", 0.5), _leg("buy", "PE", 0.5)])
    )


def test_templates_have_no_structure_problems():
    from algoverse.lab.seeds import TEMPLATES
    from algoverse.lab.spec import structure_problems

    assert {
        spec.name: structure_problems(spec) for spec in TEMPLATES if structure_problems(spec)
    } == {}

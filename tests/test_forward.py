import json
import shutil
from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from algoverse.ai.schemas import OK, AiReview
from algoverse.backtest.registry import Registry
from algoverse.execution.ledger import CLOSED, OPEN, PENDING
from algoverse.forward.drift import drift_checks, extract_baseline
from algoverse.forward.gates import GateInputs, evaluate_gates, load_gates
from algoverse.forward.runs import (
    ACTIVE,
    CHAMPION,
    ENDED,
    HALTED,
    ForwardManager,
    code_fingerprint,
    config_fingerprints,
)
from algoverse.forward.shadow import (
    AI_APPROVED,
    AI_VETO,
    FILTER,
    PASSED_OVER,
    RISK,
    as_one_lot,
    classify,
    select,
)
from algoverse.risk.limits import RiskDecision
from algoverse.runtime import Runtime
from algoverse.settings import Settings
from conftest import CONFIG_DIR, NOW, later, make_bars, make_contracts, make_raw_chain

GATES = load_gates(CONFIG_DIR)


@pytest.fixture
def forward_env(tmp_path):
    config_dir = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config_dir)
    events = config_dir / "events.yaml"
    events.write_text(
        events.read_text().replace("reviewed_through: null", "reviewed_through: 2030-01-01")
    )
    settings = Settings(
        _env_file=None, data_dir=tmp_path / "data", config_dir=config_dir, ai_enabled=False
    )
    runtime = Runtime(settings)
    runtime.store.upsert_candles("NIFTY", make_bars())
    runtime.store.upsert_candles("INDIAVIX", make_bars(start=14.0, drift=0.0).assign(close=14.0))
    return SimpleNamespace(settings=settings, config_dir=config_dir, manager=runtime.forward)


def snapshot(minutes=0):
    raw = make_raw_chain(now=later(minutes))
    return {"NIFTY": raw}, make_contracts(raw), later(minutes)


def brokers(env, run):
    live = env.manager._load_live()
    return env.manager._broker(run, live)[0], env.manager._shadow_broker(run, live)


def change_weight(env):
    path = env.config_dir / "scoring.yaml"
    path.write_text(
        path.read_text().replace("trend_alignment: {w: 4}", "trend_alignment: {w: 5}", 1)
    )


def test_code_fingerprint_changes_only_with_decision_code(tmp_path):
    root = tmp_path / "pkg"
    for package in ("ai", "analytics", "evaluation", "execution", "risk", "strategies", "api"):
        (root / package).mkdir(parents=True)
    (root / "risk" / "a.py").write_text("x = 1")
    (root / "api" / "b.py").write_text("y = 1")
    first = code_fingerprint(root)
    (root / "api" / "b.py").write_text("y = 2")
    assert code_fingerprint(root) == first
    (root / "risk" / "a.py").write_text("x = 2")
    assert code_fingerprint(root) != first


def test_event_calendar_updates_are_not_a_strategy_change(forward_env):
    before = config_fingerprints(forward_env.config_dir)
    (forward_env.config_dir / "events.yaml").write_text(
        "reviewed_through: 2031-01-01\nevents: []\n"
    )
    assert config_fingerprints(forward_env.config_dir) == before


def test_run_locks_parameters_and_copies_gates(forward_env):
    run = forward_env.manager.start("base", NOW, CHAMPION, 55.0, ("long_vol",))
    assert (run.status, run.version, run.min_score) == (ACTIVE, 1, 55.0)
    assert run.disabled_profiles == ["long_vol"]
    assert run.gates["min_trades"] == GATES.min_trades
    gates = forward_env.config_dir / "gates.yaml"
    gates.write_text(gates.read_text().replace("min_trades: 40", "min_trades: 1"))
    assert forward_env.manager.store.run(run.id).gates["min_trades"] == GATES.min_trades


def test_only_one_champion_can_be_active(forward_env):
    forward_env.manager.start("a", NOW, CHAMPION)
    with pytest.raises(ValueError, match="champion"):
        forward_env.manager.start("b", NOW, CHAMPION)
    assert forward_env.manager.start("b", NOW, "challenger").status == ACTIVE


def test_changing_a_weight_ends_the_run_and_starts_a_new_version(forward_env):
    manager = forward_env.manager
    run = manager.start("base", NOW, CHAMPION, 0.0)
    manager.on_cycle(*snapshot(0))
    manager.on_cycle(*snapshot(5))
    broker, _ = brokers(forward_env, run)
    assert broker.ledger.positions((OPEN,))

    change_weight(forward_env)
    manager.on_cycle(*snapshot(10))

    ended = manager.store.run(run.id)
    assert ended.status == ENDED and "scoring.yaml" in ended.end_reason
    assert not broker.ledger.positions((OPEN, PENDING))
    current = manager.store.runs(ACTIVE)
    assert len(current) == 1
    assert (current[0].name, current[0].version, current[0].role) == ("base", 2, CHAMPION)
    assert current[0].min_score == 0.0
    assert current[0].fingerprint != run.fingerprint
    assert current[0].fingerprint == manager.fingerprint()


def test_runs_share_data_but_not_capital(forward_env):
    manager = forward_env.manager
    a = manager.start("a", NOW, CHAMPION, 0.0)
    b = manager.start("b", NOW, "challenger", 101.0)
    manager.on_cycle(*snapshot(0))
    manager.on_cycle(*snapshot(5))
    assert brokers(forward_env, a)[0].ledger.positions((OPEN,))
    assert not brokers(forward_env, b)[0].ledger.positions((OPEN, PENDING))


def test_fills_happen_on_the_next_snapshot_and_are_measured(forward_env):
    manager = forward_env.manager
    run = manager.start("a", NOW, CHAMPION, 0.0)
    manager.on_cycle(*snapshot(0))
    broker = brokers(forward_env, run)[0]
    assert broker.ledger.positions((PENDING,)) and not broker.ledger.positions((OPEN,))
    manager.on_cycle(*snapshot(5))
    assert broker.ledger.positions((OPEN,))
    ratio, fills = manager.store.slippage(run.id)
    assert fills >= 1 and ratio is not None
    assert manager.store.labels(run.id)


def test_drawdown_limit_halts_and_flattens(forward_env):
    manager = forward_env.manager
    run = manager.start("a", NOW, CHAMPION, 0.0)
    manager.store.update(run.id, gates=json.dumps(run.gates | {"max_drawdown_pct": 0.0001}))
    manager.on_cycle(*snapshot(0))
    manager.on_cycle(*snapshot(5))
    manager.on_cycle(*snapshot(10))
    halted = manager.store.run(run.id)
    assert halted.status == HALTED and "Drawdown" in halted.end_reason
    assert not brokers(forward_env, halted)[0].ledger.positions((OPEN, PENDING))
    assert manager.store.runs(ACTIVE) == []


def test_one_failing_run_does_not_stop_others(forward_env, monkeypatch):
    manager = forward_env.manager
    a = manager.start("a", NOW, CHAMPION, 0.0)
    b = manager.start("b", NOW, "challenger", 0.0)
    original = manager._process

    def flaky(run, *args):
        if run.id == a.id:
            raise RuntimeError("broken")
        return original(run, *args)

    monkeypatch.setattr(manager, "_process", flaky)
    assert list(manager.on_cycle(*snapshot(0))) == [b.id]


# ---- Shadow tracking ---------------------------------------------------------------------------


def idea_for(forward_env):
    manager = forward_env.manager
    run = manager.start("a", NOW, CHAMPION, 101.0)
    return run, manager.on_cycle(*snapshot(0))[run.id].ideas


def test_classification_of_each_stage(forward_env):
    _, ideas = idea_for(forward_env)
    tradable = next(i for i in ideas if i.tradable)
    assert classify(tradable)[0] == PASSED_OVER
    reviewed = replace(tradable, ai=AiReview(status=OK, model="m"))
    assert classify(reviewed)[0] == AI_APPROVED
    vetoed = replace(tradable, rejections=("AI veto: conflict",))
    assert classify(vetoed) == (AI_VETO, "AI veto: conflict")
    assert classify(replace(tradable, rejections=("spread too wide",)))[0] == FILTER
    denied = replace(tradable, risk=RiskDecision(False, 0, 1.0, 1.0, ("margin limit",)))
    assert classify(denied) == (RISK, "margin limit")


def test_shadow_position_is_one_lot_and_tradable(forward_env):
    _, ideas = idea_for(forward_env)
    rejected = next(i for i in ideas if i.rejections or (i.risk and not i.risk.approved))
    followed = as_one_lot(rejected)
    assert followed.tradable and followed.risk.lots == 1
    assert followed.candidate == rejected.candidate


def test_selection_rotates_stages_and_respects_caps(forward_env):
    _, ideas = idea_for(forward_env)
    tradable = [i for i in ideas if i.tradable][:3]
    vetoed = [replace(i, rejections=("AI veto: x",)) for i in tradable]
    mixed = vetoed + [i for i in ideas if not i.tradable][:10]
    config = GATES.shadow.model_copy(update={"max_new_per_cycle": 2})
    picked = select(mixed, set(), 0, config)
    assert len(picked) == 2 and picked[0][1] == AI_VETO and picked[1][1] != AI_VETO
    assert select(mixed, set(), config.max_new_per_day, config) == []


def test_shadow_follows_ideas_without_touching_the_real_run(forward_env):
    manager = forward_env.manager
    run = manager.start("a", NOW, CHAMPION, 101.0)
    manager.on_cycle(*snapshot(0))
    manager.on_cycle(*snapshot(5))
    real, shadow = brokers(forward_env, run)
    assert not real.ledger.positions((OPEN, PENDING))
    assert shadow.ledger.positions((OPEN,))
    assert all(p.lots == 1 for p in shadow.ledger.positions((OPEN,)))
    stages = manager.store.shadow_stages(run.id)
    assert set(stages.values()) <= {RISK, FILTER, PASSED_OVER}


def test_the_same_idea_is_followed_once_per_day(forward_env):
    manager = forward_env.manager
    run = manager.start("a", NOW, CHAMPION, 101.0)
    for minutes in (0, 5, 10):
        manager.on_cycle(*snapshot(minutes))
    with manager.store._connect() as connection:
        rows = connection.execute(
            "SELECT idea_id, COUNT(*) c FROM shadow_meta WHERE run_id = ? GROUP BY idea_id",
            (run.id,),
        ).fetchall()
    assert rows and all(row["c"] == 1 for row in rows)


def test_ai_value_needs_enough_samples():
    rows = [
        {"stage": "ai_approved", "trades": 12, "expectancy": 300.0},
        {"stage": "ai_veto", "trades": 11, "expectancy": -100.0},
    ]
    assert ForwardManager._ai_value(rows, 10)["benefit_per_trade"] == 400.0
    thin = ForwardManager._ai_value(rows, 20)
    assert not thin["enough_data"] and thin["benefit_per_trade"] is None


# ---- Drift ---------------------------------------------------------------------------------------

BASE = {
    "trades": 200,
    "win_rate": 0.5,
    "expectancy": 100.0,
    "pnl_std": 400.0,
    "drawdown_p95": 5000.0,
}


def trades(pnl):
    return pd.DataFrame({"net_pnl": pnl})


def by_key(checks):
    return {check["key"]: check for check in checks}


def test_drift_range_hand_calculation():
    checks = by_key(
        drift_checks(BASE, trades([100.0, 200.0, 0.0, -100.0] * 25), 1000.0, 0.8, 50, GATES)
    )
    # 2 standard errors on 100 trades: win rate 0.5 +/- 0.1, expectancy 100 +/- 80.
    assert checks["win_rate"]["low"] == pytest.approx(0.4)
    assert checks["win_rate"]["high"] == pytest.approx(0.6)
    assert checks["expectancy"]["low"] == pytest.approx(20.0)
    assert checks["expectancy"]["high"] == pytest.approx(180.0)
    assert {c["status"] for c in checks.values()} == {"in"}


def test_results_better_than_expected_are_also_flagged():
    checks = by_key(drift_checks(BASE, trades([1000.0] * 50), 0.0, 0.8, 50, GATES))
    assert checks["expectancy"]["status"] == "out"
    assert checks["win_rate"]["status"] == "out"


def test_drawdown_and_slippage_breaches_are_flagged():
    checks = by_key(drift_checks(BASE, trades([100.0] * 20), 6000.0, 1.3, 50, GATES))
    assert checks["drawdown"]["status"] == "out"
    assert checks["slippage"]["status"] == "out"


def test_small_samples_and_missing_baselines_are_not_judged():
    checks = by_key(drift_checks(BASE, trades([100.0] * 3), 0.0, 0.8, 3, GATES))
    assert checks["expectancy"]["status"] == "insufficient_data"
    assert checks["slippage"]["status"] == "insufficient_data"
    checks = by_key(drift_checks(None, trades([100.0] * 50), 0.0, 0.8, 50, GATES))
    assert checks["expectancy"]["status"] == "no_baseline"
    assert checks["drawdown"]["status"] == "no_baseline"


def test_baseline_extraction():
    report = {
        "summary": {
            "overall": {"trades": 5, "win_rate": 0.4, "expectancy": 9.0, "pnl_std": 3.0},
            "monte_carlo": {"drawdown_p95": 77.0},
        }
    }
    assert extract_baseline(report)["drawdown_p95"] == 77.0
    assert extract_baseline(None) is None
    assert extract_baseline({"summary": {"overall": {"trades": 0}}}) is None


# ---- Promotion gate ------------------------------------------------------------------------------

PASSING = GateInputs(
    backtest_verdict="passed_walk_forward",
    holdout_verdict="passed_holdout",
    reports_match_config=True,
    weeks=9.0,
    trades=45,
    volatility_regimes=2,
    expiries=7,
    expectancy=150.0,
    expectancy_status="in",
    drawdown_pct=3.0,
    slippage_ratio=0.9,
)


def test_all_gates_pass_when_every_requirement_is_met():
    assert all(gate["passed"] for gate in evaluate_gates(PASSING, GATES))


@pytest.mark.parametrize(
    ("change", "failed"),
    [
        ({"backtest_verdict": None}, "walk_forward"),
        ({"backtest_verdict": "failed"}, "walk_forward"),
        ({"holdout_verdict": "failed"}, "holdout"),
        ({"reports_match_config": False}, "same_config"),
        ({"weeks": 7.9}, "weeks"),
        ({"trades": 39}, "trades"),
        ({"volatility_regimes": 1}, "regimes"),
        ({"expiries": 5}, "expiries"),
        ({"expectancy": -1.0}, "expectancy_positive"),
        ({"expectancy": None}, "expectancy_positive"),
        ({"expectancy_status": "out"}, "expectancy_in_range"),
        ({"expectancy_status": "no_baseline"}, "expectancy_in_range"),
        ({"drawdown_pct": 8.0}, "drawdown"),
        ({"slippage_ratio": 1.01}, "slippage"),
        ({"slippage_ratio": None}, "slippage"),
    ],
)
def test_each_gate_can_fail_alone(change, failed):
    gates = evaluate_gates(replace(PASSING, **change), GATES)
    assert [gate["key"] for gate in gates if not gate["passed"]] == [failed]


def report(kind, digest, verdict, report_id):
    return {
        "id": report_id,
        "kind": kind,
        "created_at": NOW,
        "config_hash": digest,
        "verdict": verdict,
        "window": {},
        "trials_to_date": 1,
        "summary": {
            "overall": {"trades": 200, "win_rate": 0.5, "expectancy": 100.0, "pnl_std": 300.0},
            "monte_carlo": {"drawdown_p95": 20000.0},
            "warnings": [],
        },
    }


WALK = "walk_forward-20261109-120000-abc123"
HOLD = "holdout-20261109-120000-abc124"


def test_attaching_evidence_never_changes_trading_and_checks_the_kind(forward_env):
    manager = forward_env.manager
    run = manager.start("a", NOW, CHAMPION, 60.0)
    registry = Registry(forward_env.settings.data_dir)
    registry.save_report(report("walk_forward", run.backtest_hash, "passed_walk_forward", WALK))
    registry.save_report(report("holdout", run.backtest_hash, "passed_holdout", HOLD))
    with pytest.raises(ValueError):
        manager.attach(run.id, HOLD, None)
    with pytest.raises(ValueError):
        manager.attach(run.id, "walk_forward-20261109-120000-ffffff", None)
    updated = manager.attach(run.id, WALK, HOLD)
    assert updated.baseline["expectancy"] == 100.0
    assert (updated.min_score, updated.fingerprint) == (60.0, run.fingerprint)
    evidence = manager._evidence(updated, brokers(forward_env, updated)[0], NOW)
    gates = {gate["key"]: gate["passed"] for gate in evidence["gates"]}
    assert gates["walk_forward"] and gates["holdout"] and gates["same_config"]
    assert not gates["trades"]


def test_reports_from_a_different_configuration_do_not_count(forward_env):
    manager = forward_env.manager
    run = manager.start("a", NOW, CHAMPION)
    registry = Registry(forward_env.settings.data_dir)
    registry.save_report(report("walk_forward", "different", "passed_walk_forward", WALK))
    registry.save_report(report("holdout", "different", "passed_holdout", HOLD))
    updated = manager.attach(run.id, WALK, HOLD)
    evidence = manager._evidence(updated, brokers(forward_env, updated)[0], NOW)
    assert not {g["key"]: g["passed"] for g in evidence["gates"]}["same_config"]


def test_settlement_after_close_processes_real_and_shadow_books(forward_env):
    manager = forward_env.manager
    run = manager.start("a", NOW, CHAMPION, 0.0)
    manager.on_cycle(*snapshot(0))
    manager.on_cycle(*snapshot(5))
    real, shadow = brokers(forward_env, run)
    assert real.ledger.positions((OPEN,))
    manager.after_close(datetime(2027, 2, 1, 15, 35, tzinfo=NOW.tzinfo))
    assert not real.ledger.positions((OPEN,))
    assert real.ledger.positions((CLOSED,))
    assert shadow.ledger.positions((CLOSED,))


def test_overview_and_api(forward_env):
    from algoverse.api.app import create_app
    from fastapi.testclient import TestClient

    client = TestClient(create_app(forward_env.settings))
    assert client.get("/api/forward").json() == {"runs": []}
    manager = forward_env.manager
    run = manager.start("a", NOW, CHAMPION, 0.0)
    manager.on_cycle(*snapshot(0))
    manager.on_cycle(*snapshot(5))
    body = client.get("/api/forward").json()["runs"][0]
    assert body["id"] == run.id and body["locked_inputs_current"] is True
    assert len(body["gates"]) == 11 and len(body["drift"]) == 4
    assert {row["stage"] for row in body["shadow"]} >= {"ai_veto", "ai_approved"}
    assert body["gate_limits"]["max_drawdown_pct"] == GATES.max_drawdown_pct
    change_weight(forward_env)
    assert client.get("/api/forward").json()["runs"][0]["locked_inputs_current"] is False


def test_no_time_passes_incorrectly_in_weeks(forward_env):
    manager = forward_env.manager
    run = manager.start("a", NOW, CHAMPION)
    evidence = manager._evidence(run, brokers(forward_env, run)[0], NOW + timedelta(days=14))
    assert evidence["weeks"] == pytest.approx(2.0)
    assert date.fromisoformat(run.started_at[:10]) == NOW.date()

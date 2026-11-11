import json
import shutil
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from algoverse.api.app import create_app
from algoverse.backtest.config import LabConfig, load_backtest_config
from algoverse.backtest.dataview import History
from algoverse.lab.evaluate import FAILED, FAILS_2X, HELD_UP, TOO_FEW, UNPROVEN, verdict
from algoverse.lab.evidence import luck_adjusted_floor, prob_better
from algoverse.lab.ladder import (
    EXIT,
    FILTER,
    ROOTS,
    STRUCTURE,
    TIMING,
    judge_step,
    ladder_windows,
    propose_steps,
)
from algoverse.lab.service import LabService
from algoverse.lab.spec import (
    Condition,
    StrategySpec,
    complexity_level,
    describe_entry,
    entry_failures,
    signal_value,
    signature,
)
from algoverse.settings import Settings
from conftest import CONFIG_DIR
from fastapi.testclient import TestClient
from test_backtest import market_rows, save, weekdays
from test_lab import HEADER, NOW

CALL = ROOTS[0]
PUT = ROOTS[1]
STRADDLE = ROOTS[2]


def days(values):
    start = date(2026, 1, 1)
    return {str(start + timedelta(days=i)): float(v) for i, v in enumerate(values)}


def test_floor_is_stricter_with_more_trials():
    pnls = list(np.random.default_rng(1).normal(100, 400, 60))
    assert luck_adjusted_floor(pnls, 100)["floor"] < luck_adjusted_floor(pnls, 1)["floor"]


def test_floor_is_repeatable_and_needs_data():
    pnls = [100.0, -50.0, 80.0, 20.0]
    assert luck_adjusted_floor(pnls, 3) == luck_adjusted_floor(pnls, 3)
    assert luck_adjusted_floor([100.0], 3) is None
    assert luck_adjusted_floor(pnls, 3)["trials"] == 3


def test_a_clear_edge_has_a_positive_floor():
    assert luck_adjusted_floor([100.0 + i for i in range(50)], 20)["floor"] > 0


def test_prob_better_separates_real_gains_from_noise():
    rng = np.random.default_rng(3)
    parent = days(rng.normal(0, 100, 80))
    better = {day: value + 200 for day, value in parent.items()}
    assert prob_better(better, parent) > 0.99
    assert prob_better(parent, better) < 0.01
    assert prob_better(parent, parent) == 0.0


def test_prob_better_counts_days_only_one_traded():
    assert prob_better({"a": 100.0, "b": 100.0}, {"c": 100.0, "d": 100.0}) < 1.0


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"t": 5, "n": 100.0, "d": 50.0, "f": 10.0}, TOO_FEW),
        ({"t": 20, "n": -1.0, "d": -5.0, "f": 10.0}, FAILED),
        ({"t": 20, "n": 100.0, "d": -5.0, "f": 10.0}, FAILS_2X),
        ({"t": 20, "n": 100.0, "d": 50.0, "f": -3.0}, UNPROVEN),
        ({"t": 20, "n": 100.0, "d": 50.0, "f": 3.0}, HELD_UP),
    ],
)
def test_verdict_needs_more_than_a_profit(result, expected):
    test = {"trades": result["t"], "net_pnl": result["n"]}
    assert verdict(test, {"net_pnl": result["d"]}, 15, result["f"]) == expected


def node(sharpe, net, trades=20, expectancy=10.0, daily=None):
    summary = {"sharpe": sharpe, "net_pnl": net, "trades": trades, "expectancy": expectancy}
    return {"design": summary, "validate": summary, "validate_daily": daily or {}}


CONFIG = LabConfig(ladder_min_trades=8, ladder_alpha=0.2)
GOOD = days(np.random.default_rng(5).normal(500, 100, 80))
FLAT = days(np.random.default_rng(6).normal(0, 100, 80))
NOISE = dict(zip(FLAT, np.random.default_rng(9).normal(0, 100, 80), strict=True))


def test_a_real_improvement_is_accepted():
    accepted, _, chance = judge_step(
        node(2.0, 1000, daily=GOOD), node(0.5, 100, daily=FLAT), 5, CONFIG
    )
    assert accepted and chance > 0.96


@pytest.mark.parametrize(
    ("child", "reason"),
    [
        (node(2.0, 1000, trades=3, daily=GOOD), "too few trades"),
        (node(2.0, -5, daily=GOOD), "loses money"),
        (node(0.1, 500, daily=GOOD), "no better"),
        (node(2.0, 1000, expectancy=-1.0, daily=GOOD), "only looks better"),
        (node(2.0, 1000, daily=NOISE), "could be luck"),
    ],
)
def test_a_change_must_earn_its_place(child, reason):
    accepted, text, _ = judge_step(child, node(0.5, 100, daily=FLAT), 5, CONFIG)
    assert not accepted and reason in text


def test_trying_more_changes_raises_the_bar():
    daily = {day: value + 30 for day, value in NOISE.items()}
    few = judge_step(node(2.0, 1000, daily=daily), node(0.5, 100, daily=FLAT), 1, CONFIG)
    many = judge_step(node(2.0, 1000, daily=daily), node(0.5, 100, daily=FLAT), 200, CONFIG)
    assert few[0] and not many[0]


def changes(parent, child):
    keys = 0
    keys += parent.legs != child.legs
    keys += parent.exits != child.exits
    before, after = parent.entry.model_dump(), child.entry.model_dump()
    dte = (before["dte_min"], before["dte_max"]) != (after["dte_min"], after["dte_max"])
    keys += dte
    keys += sum(
        before[name] != after[name]
        for name in ("trend", "iv_rank_min", "iv_rank_max", "conditions")
    )
    return keys


@pytest.mark.parametrize("root", ROOTS)
def test_every_step_changes_exactly_one_idea(root):
    steps = propose_steps(root, 30)
    assert steps
    for label, _, child in steps:
        assert changes(root, child) == 1, label
        assert child.parent_id == root.id and child.rung == 2 and child.step == label


def test_filters_follow_the_strategys_direction():
    call_steps = [label for label, _, _ in propose_steps(CALL, 30)]
    put_steps = [label for label, _, _ in propose_steps(PUT, 30)]
    assert "only in an uptrend" in call_steps and "only in a downtrend" not in call_steps
    assert "only in a downtrend" in put_steps


def test_steps_include_structure_exits_and_timing_when_limited():
    kinds = {kind for _, kind, _ in propose_steps(CALL, 10)}
    assert kinds == {FILTER, STRUCTURE, EXIT, TIMING}


def test_a_long_option_can_climb_to_a_spread_then_a_butterfly():
    spread = next(c for _, k, c in propose_steps(CALL, 30) if k == STRUCTURE)
    assert len(spread.legs) == 2 and spread.legs[1].action == "sell"
    butterfly = next(c for _, k, c in propose_steps(spread, 30) if k == STRUCTURE)
    assert [leg.ratio for leg in butterfly.legs] == [1, 2, 1]
    assert spread.rung == 2 and butterfly.rung == 3 and butterfly.parent_id == spread.id


def test_steps_do_not_repeat_a_tested_strategy():
    seen = {signature(CALL)}
    first = propose_steps(CALL, 30, seen)
    assert propose_steps(CALL, 30, seen) == []
    assert len({signature(child) for _, _, child in first}) == len(first)


def test_a_condition_already_used_is_not_proposed_again():
    child = next(c for label, _, c in propose_steps(CALL, 30) if "OI build-up" in label)
    assert not any("OI build-up" in label for label, _, _ in propose_steps(child, 30))


def test_ladder_children_are_valid_ids():
    for _, _, child in propose_steps(STRADDLE, 30):
        StrategySpec.model_validate(child.model_dump())


def test_complexity_grows_with_legs_and_conditions():
    assert complexity_level(CALL) == "simple"
    filtered = CALL.model_copy(
        update={
            "entry": CALL.entry.model_copy(
                update={
                    "trend": ["up"],
                    "conditions": [Condition(signal="pcr", op="above", value=1.0)],
                }
            )
        }
    )
    assert complexity_level(filtered) == "moderate"
    assert complexity_level(STRADDLE) == "moderate"
    many = filtered.entry.model_copy(
        update={
            "conditions": [
                *filtered.entry.conditions,
                Condition(signal="adx", op="above", value=20),
            ]
        }
    )
    assert complexity_level(filtered.model_copy(update={"entry": many})) == "complex"


EXPIRY = date(2026, 11, 17)


def context(**changes):
    stats = SimpleNamespace(atm_iv=0.18, skew=0.02, pcr=1.2, buildup=0.4, max_pain=24000.0)
    ranking = SimpleNamespace(rank=30.0)
    base = {
        "regime": SimpleNamespace(trend="up", adx=28.0, gap_pct=0.5, range_pct=1.1),
        "iv": SimpleNamespace(ranking=ranking),
        "vix_ranking": SimpleNamespace(rank=60.0),
        "vix_change_pct": -2.0,
        "term_slope": 0.01,
        "realised_vol": 0.15,
        "spot": 24240.0,
        "by_expiry": {EXPIRY: stats},
    }
    return SimpleNamespace(**(base | changes))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("iv_rank", 30.0),
        ("vix_rank", 60.0),
        ("vix_change_pct", -2.0),
        ("iv_minus_rv", 3.0),
        ("term_slope", 0.01),
        ("pcr", 1.2),
        ("skew", 2.0),
        ("oi_buildup", 0.4),
        ("spot_vs_max_pain_pct", 1.0),
        ("adx", 28.0),
        ("gap_pct", 0.5),
        ("range_pct", 1.1),
    ],
)
def test_signals_read_the_market_context(name, expected):
    assert signal_value(name, context(), EXPIRY) == pytest.approx(expected)


def conditional(*conditions):
    entry = CALL.entry.model_copy(update={"conditions": list(conditions)})
    return CALL.model_copy(update={"entry": entry})


def test_all_conditions_must_hold():
    passes = conditional(
        Condition(signal="pcr", op="above", value=1.0),
        Condition(signal="adx", op="above", value=25.0),
    )
    assert entry_failures(passes, context(), EXPIRY) == []
    fails = conditional(
        Condition(signal="pcr", op="above", value=1.0),
        Condition(signal="adx", op="below", value=25.0),
    )
    assert len(entry_failures(fails, context(), EXPIRY)) == 1


def test_an_unavailable_signal_blocks_the_trade():
    spec = conditional(Condition(signal="vix_rank", op="above", value=1.0))
    failures = entry_failures(spec, context(vix_ranking=None), EXPIRY)
    assert failures and "not available" in failures[0]


def test_conditions_change_the_signature_and_description():
    spec = conditional(Condition(signal="pcr", op="above", value=1.0))
    assert signature(spec) != signature(CALL)
    assert "Put-call ratio above 1" in describe_entry(spec)


def test_too_many_conditions_are_refused():
    condition = {"signal": "pcr", "op": "above", "value": 1.0}
    with pytest.raises(ValueError):
        StrategySpec.model_validate(
            {
                **CALL.model_dump(),
                "entry": {**CALL.entry.model_dump(), "conditions": [condition] * 7},
            }
        )


@pytest.fixture(scope="module")
def ladder_dirs(tmp_path_factory):
    root = tmp_path_factory.mktemp("ladder")
    config_dir = root / "config"
    shutil.copytree(CONFIG_DIR, config_dir)
    text = (config_dir / "backtest.yaml").read_text()
    for old, new in (
        ("symbols: [NIFTY, BANKNIFTY, FINNIFTY]", "symbols: [NIFTY]"),
        ("warmup_sessions: 60", "warmup_sessions: 55"),
        ("min_sessions: 60", "min_sessions: 50"),
        ("min_test_trades: 15", "min_test_trades: 1"),
        ("workers: 6", "workers: 1"),
        ("ladder_rungs: 4", "ladder_rungs: 2"),
        ("ladder_beam: 2", "ladder_beam: 1"),
        ("ladder_steps: 10", "ladder_steps: 2"),
        ("ladder_min_trades: 8", "ladder_min_trades: 1"),
    ):
        assert old in text
        text = text.replace(old, new)
    (config_dir / "backtest.yaml").write_text(text)
    events = config_dir / "events.yaml"
    events.write_text(
        events.read_text().replace("reviewed_through: null", "reviewed_through: 2030-01-01")
    )
    data = root / "data"
    rng = np.random.default_rng(31)
    sessions = weekdays("2026-01-05", 125)
    save(data, market_rows(sessions, 23000 + np.cumsum(rng.normal(8, 100, len(sessions)))))
    return data, config_dir


def fresh(ladder_dirs, tmp_path):
    data, config_dir = ladder_dirs
    copy = tmp_path / "data"
    shutil.copytree(data, copy)
    return LabService(copy, config_dir, False, None)


def test_windows_run_in_order_without_overlap(ladder_dirs):
    data, config_dir = ladder_dirs
    windows = ladder_windows(History(data).sessions, load_backtest_config(config_dir))
    assert windows.design_end < windows.validate_start
    assert windows.validate_end < windows.final_start
    assert windows.design_sessions >= windows.validate_sessions


def test_too_little_history_is_refused(ladder_dirs):
    data, config_dir = ladder_dirs
    assert ladder_windows(History(data).sessions[:60], load_backtest_config(config_dir)) is None


@pytest.fixture(scope="module")
def climbed(ladder_dirs, tmp_path_factory):
    lab = fresh(ladder_dirs, tmp_path_factory.mktemp("climb"))
    notes = []
    report = lab.run_ladder(None, NOW, notes.append)
    return lab, report, notes


def test_ladder_reports_each_rung(climbed):
    _, report, notes = climbed
    assert report["rungs"][0]["rung"] == 1
    assert len(report["rungs"][0]["tried"]) == 3
    assert report["rungs"][0]["tried"][0]["level"] in ("simple", "moderate")
    assert report["trials"] >= 3 and notes[0].startswith("Rung 1")
    json.dumps(report, allow_nan=False)


def test_climb_never_touches_the_final_period(climbed):
    _, report, _ = climbed
    final_start = report["windows"]["final_start"]
    assert report["windows"]["validate_end"] < final_start
    for rung in report["rungs"]:
        for node_ in rung["tried"]:
            assert "test" not in node_ and "final" not in node_


def test_only_the_winner_reaches_the_final_test(climbed):
    lab, report, _ = climbed
    assert report["winner_id"] is not None
    assert report["final"]["verdict"] in (HELD_UP, UNPROVEN, FAILS_2X, FAILED, TOO_FEW)
    assert report["final_period_uses"] == 1
    winner = lab.spec(report["winner_id"])
    assert winner.source == "ladder" and lab.result(winner) is not None
    tested = [spec for spec in lab.specs() if lab.result(spec) is not None]
    assert [spec.id for spec in tested] == [winner.id]


def test_lineage_runs_from_root_to_winner(climbed):
    _, report, _ = climbed
    lineage = report["lineage"]
    assert lineage[0]["rung"] == 1 and lineage[-1]["id"] == report["winner_id"]
    for parent, child in zip(lineage, lineage[1:], strict=False):
        assert child["parent_id"] == parent["id"] and child["rung"] == parent["rung"] + 1


def test_overview_carries_the_ladder_and_signals(climbed):
    lab, report, _ = climbed
    view = lab.overview()
    assert view["ladder"]["winner_id"] == report["winner_id"]
    assert view["ladder_ready"] is True
    assert {"name": "pcr", "label": "Put-call ratio"} in view["signals"]
    assert view["symbols"] == ["NIFTY"]
    row = next(r for r in view["strategies"] if r["spec"]["id"] == report["winner_id"])
    assert row["level"] and row["entry_text"]


def test_ladder_can_start_from_a_chosen_strategy(ladder_dirs, tmp_path):
    lab = fresh(ladder_dirs, tmp_path)
    report = lab.run_ladder("tpl_long_call", NOW)
    assert len(report["rungs"][0]["tried"]) == 1
    assert report["rungs"][0]["tried"][0]["id"] == "tpl_long_call"


def test_api_needs_the_dashboard_and_enough_history(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, ai_enabled=False)
    client = TestClient(create_app(settings))
    assert client.post("/api/lab/ladder", json={}).status_code == 403
    response = client.post("/api/lab/ladder", json={}, headers=HEADER)
    assert response.status_code in (400, 409) and "history" in response.json()["detail"]
    assert client.get("/api/lab").json()["ladder"] is None

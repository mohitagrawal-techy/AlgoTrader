import json
import shutil
from datetime import date, datetime

import numpy as np
import pytest
from algoverse.ai.client import LLMError, LLMResult
from algoverse.api.app import create_app
from algoverse.backtest.config import load_backtest_config
from algoverse.backtest.dataview import History
from algoverse.backtest.engine import ReplaySpec, Services, replay
from algoverse.evaluation.config import EventCalendar
from algoverse.lab.designer import SYSTEM, feedback_row
from algoverse.lab.evaluate import (
    FAILED,
    FAILS_2X,
    HELD_UP,
    TOO_FEW,
    UNPROVEN,
    normalize_reason,
    rank_key,
    split_windows,
    verdict,
)
from algoverse.lab.seeds import TEMPLATES
from algoverse.lab.service import LabError, LabService
from algoverse.lab.spec import (
    CustomSet,
    StrategySpec,
    apply_specs,
    describe_legs,
    has_undefined_risk,
    signature,
)
from algoverse.settings import Settings
from conftest import CONFIG_DIR, IST
from fastapi.testclient import TestClient
from test_backtest import market_rows, save, weekdays

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=IST)
HEADER = {"X-AlgoVerse-Client": "dashboard"}
SPREAD = {
    "name": "Tight bull call",
    "bias": "bullish",
    "legs": [
        {"action": "buy", "option_type": "CE", "select": "atm_offset", "value": 0},
        {"action": "sell", "option_type": "CE", "select": "atm_offset", "value": 2},
    ],
    "entry": {"dte_min": 3, "dte_max": 20},
    "exits": {"target_frac": 0.6, "stop_frac": 0.5, "exit_dte": 1},
}


def spec(**changes):
    return StrategySpec.model_validate({"id": "s1", **SPREAD, **changes})


@pytest.mark.parametrize(
    "changes",
    [
        {"legs": []},
        {"legs": [SPREAD["legs"][0]] * 5},
        {"legs": [{"action": "buy", "option_type": "CE", "select": "delta", "value": 1.5}]},
        {"legs": [{"action": "buy", "option_type": "CE", "select": "atm_offset", "value": 0.5}]},
        {"legs": [{"action": "hold", "option_type": "CE", "select": "delta", "value": 0.5}]},
        {"entry": {"dte_min": 10, "dte_max": 5}},
        {"entry": {"iv_rank_min": 80, "iv_rank_max": 20}},
        {"entry": {"dte_min": 1}, "exits": {"exit_dte": 1}},
        {"exits": {"stop_frac": 0}},
        {"id": "Bad Id!"},
    ],
)
def test_invalid_strategies_are_refused(changes):
    with pytest.raises(ValueError):
        spec(**changes)


def test_every_template_is_valid_and_unique():
    assert len({t.id for t in TEMPLATES}) == len(TEMPLATES) == 10
    assert len({signature(t) for t in TEMPLATES}) == 10
    assert all(t.source == "template" for t in TEMPLATES)


def test_signature_ignores_name_and_leg_order():
    reordered = spec(name="Other", legs=list(reversed(SPREAD["legs"])))
    assert signature(reordered) == signature(spec())
    assert signature(spec(exits={"target_frac": 0.7, "stop_frac": 0.5, "exit_dte": 1})) != (
        signature(spec())
    )


def test_undefined_risk_detection_and_leg_text():
    assert not has_undefined_risk(spec())
    naked = spec(legs=[{"action": "sell", "option_type": "PE", "select": "delta", "value": 0.2}])
    assert has_undefined_risk(naked)
    assert describe_legs(spec()) == "+1 CE ATM, -1 CE ATM+2"


def test_reasons_are_grouped():
    assert normalize_reason("24000 CE: spread 12.5% is too wide") == "spread N is too wide"
    assert normalize_reason("per-trade loss limit: one lot uses 1.4x the room") == (
        "per-trade loss limit"
    )
    assert normalize_reason("entry rule: trend is up") == "entry rule: trend is up"


def test_verdicts():
    good, bad, none = (
        {"trades": 20, "net_pnl": 5.0},
        {"trades": 20, "net_pnl": -5.0},
        {
            "trades": 3,
            "net_pnl": 99.0,
        },
    )
    assert verdict(good, good, 15) == HELD_UP
    assert verdict(good, bad, 15) == FAILS_2X
    assert verdict(bad, bad, 15) == FAILED
    assert verdict(none, none, 15) == TOO_FEW
    held = {"verdict": HELD_UP, "test": {"sharpe": 0.5}}
    better = {"verdict": HELD_UP, "test": {"sharpe": 1.5}}
    failed = {"verdict": FAILED, "test": {"sharpe": 9.0}}
    assert sorted([failed, None, held, better], key=rank_key) == [better, held, failed, None]


def test_windows_put_the_test_period_last_without_overlap():
    config = load_backtest_config(CONFIG_DIR)
    sessions = weekdays("2025-01-01", 260)
    windows = split_windows(sessions, config)
    assert windows.train_start == sessions[config.warmup_sessions]
    assert windows.train_end < windows.test_start and windows.test_end == sessions[-1]
    assert windows.train_sessions + windows.test_sessions == 200
    assert windows.test_sessions == 60
    assert split_windows(sessions[:100], config) is None


@pytest.fixture(scope="module")
def lab_dirs(tmp_path_factory):
    root = tmp_path_factory.mktemp("lab")
    config_dir = root / "config"
    shutil.copytree(CONFIG_DIR, config_dir)
    text = (config_dir / "backtest.yaml").read_text()
    text = (
        text.replace("symbols: [NIFTY, BANKNIFTY, FINNIFTY]", "symbols: [NIFTY]")
        .replace("warmup_sessions: 60", "warmup_sessions: 55")
        .replace("min_sessions: 60", "min_sessions: 30")
        .replace("min_test_trades: 15", "min_test_trades: 1")
        .replace("workers: 10", "workers: 1")
    )
    (config_dir / "backtest.yaml").write_text(text)
    events = config_dir / "events.yaml"
    events.write_text(
        events.read_text().replace("reviewed_through: null", "reviewed_through: 2030-01-01")
    )
    data = root / "data"
    rng = np.random.default_rng(31)
    sessions = weekdays("2026-01-05", 105)
    save(data, market_rows(sessions, 23000 + np.cumsum(rng.normal(8, 100, len(sessions)))))
    return data, config_dir


def fresh(lab_dirs, tmp_path, client=None):
    data, config_dir = lab_dirs
    copy = tmp_path / "data"
    shutil.copytree(data, copy)
    return LabService(copy, config_dir, True, client)


def test_custom_strategy_trades_only_its_own_legs_and_exits(lab_dirs, risk_config, cost_model):
    from algoverse.backtest.walkforward import load_services

    data, config_dir = lab_dirs
    custom_spec = spec()
    base = load_services(config_dir)
    services = apply_specs(base, [custom_spec])
    assert "custom:s1" not in base.risk.exits and "custom:s1" in services.risk.exits
    sessions = History(data).sessions
    result = replay(
        History(data),
        services,
        ReplaySpec(sessions[60], sessions[-1], 0.0),
        custom=CustomSet([custom_spec]),
    )
    assert len(result.trades) > 0
    assert set(result.trades["strategy"]) == {"s1"}
    assert set(result.trades["profile"]) == {"custom:s1"}
    assert (result.trades["dte_at_entry"] >= 2).all()


def test_entry_rules_block_trades(lab_dirs):
    from algoverse.backtest.walkforward import load_services

    data, config_dir = lab_dirs
    never = spec(entry={"dte_min": 3, "dte_max": 20, "iv_rank_min": 99.9})
    services = apply_specs(load_services(config_dir), [never])
    sessions = History(data).sessions
    result = replay(
        History(data),
        services,
        ReplaySpec(sessions[60], sessions[-1], 0.0),
        custom=CustomSet([never]),
    )
    # A rank of 100 is reachable, so any trade must come from a high-volatility day.
    assert set(result.trades["iv_regime"]) <= {"high"}


def test_backtest_stores_results_and_ranks(lab_dirs, tmp_path):
    lab = fresh(lab_dirs, tmp_path)
    saved = lab.save(SPREAD, NOW)
    notes = []
    results = lab.run_backtest([saved.id, "tpl_call_butterfly"], NOW, notes.append)
    assert len(results) == 2 and notes[-1].startswith("Tested 2 of 2")
    result = lab.result(saved)
    assert result["train"]["trades"] > 0
    assert result["verdict"] in (HELD_UP, UNPROVEN, FAILS_2X, FAILED, TOO_FEW)
    assert result["windows"]["train_end"] < result["windows"]["test_start"]
    assert all(point["day"] >= result["windows"]["test_start"] for point in result["equity"])
    overview = lab.overview()
    assert overview["tested"] == 2 and overview["trials_logged"] == 4
    assert [bool(row["result"]) for row in overview["strategies"]][:2] == [True, True]
    json.dumps(overview, allow_nan=False)
    # The list leaves out per-trade detail; the backtest page has it, with the index bars.
    row = next(r for r in overview["strategies"] if r["spec"]["id"] == saved.id)
    assert "detail" not in row["result"] and row["result"]["has_detail"]
    page = lab.backtest_detail(saved.id)
    trades = page["result"]["detail"]["train"]["trades"]
    assert len(trades) == result["train"]["trades"]
    assert sum(page["result"]["detail"]["test"]["daily"].values()) == pytest.approx(
        result["test"]["net_pnl"]
    )
    assert set(page["bars"]) == {t["symbol"] for t in trades} and page["capital"] > 0
    json.dumps(page, allow_nan=False)
    # Every tested strategy has a research stage with the checks behind it.
    assert row["stage"]["stage"] in ("reject", "research", "paper_trade")
    assert page["stage"]["checks"] and len(page["stage"]["folds"]) == 6
    assert [s["key"] for s in overview["stages"]][-1] == "live_monitoring"
    assert "exposure_pct" in result["test"] and "recovery_factor" in result["test"]


def test_editing_rules_invalidates_the_old_result(lab_dirs, tmp_path):
    lab = fresh(lab_dirs, tmp_path)
    saved = lab.save(SPREAD, NOW)
    lab.run_backtest([saved.id], NOW)
    edited = lab.save({**SPREAD, "id": saved.id, "exits": {"target_frac": 0.9, "exit_dte": 1}}, NOW)
    assert edited.id == saved.id and lab.result(edited) is None


def test_backtest_page_for_unknown_or_untested_strategies(lab_dirs, tmp_path):
    lab = fresh(lab_dirs, tmp_path)
    with pytest.raises(LabError):
        lab.backtest_detail("nope")
    page = lab.backtest_detail("tpl_long_call")
    assert page["result"] is None and page["bars"] == {}


def test_save_delete_and_template_protection(lab_dirs, tmp_path):
    lab = fresh(lab_dirs, tmp_path)
    saved = lab.save({**SPREAD, "source": "template", "id": "tpl_long_call"}, NOW)
    assert saved.source == "user" and saved.id != "tpl_long_call"
    with pytest.raises(LabError, match="legs"):
        lab.save({**SPREAD, "legs": []}, NOW)
    with pytest.raises(LabError):
        lab.delete("tpl_long_call")
    lab.delete(saved.id)
    assert lab.spec(saved.id) is None


def test_testing_without_history_explains_what_is_missing(tmp_path):
    lab = LabService(tmp_path, CONFIG_DIR)
    with pytest.raises(LabError, match="history-import"):
        lab.run_backtest(None, NOW)
    assert lab.overview()["history"] == {
        "sessions": 0,
        "needed": 120,
        "ready": False,
        "start": None,
        "end": None,
    }


class Designer:
    model = "fake:1"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, system, user, schema):
        self.prompts.append((system, json.loads(user)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResult(json.dumps({"strategies": reply}), 10, 10)


def proposal(name, offset):
    legs = [
        {
            "action": "buy",
            "option_type": "CE",
            "select": "atm_offset",
            "value": 0,
            "ratio": 1,
            "expiry": 0,
        },
        {
            "action": "sell",
            "option_type": "CE",
            "select": "atm_offset",
            "value": offset,
            "ratio": 1,
            "expiry": 0,
        },
    ]
    return {
        "name": name,
        "rationale": "Test idea.",
        "bias": "bullish",
        "legs": legs,
        "entry": {"trend": [], "dte_min": 3, "dte_max": 20},
        "exits": {"target_frac": 0.6, "stop_frac": 0.5, "exit_dte": 1},
    }


def test_ai_loop_designs_tests_and_feeds_back_training_results_only(
    lab_dirs, tmp_path, monkeypatch
):
    monkeypatch.setattr("algoverse.lab.service.TEMPLATES", TEMPLATES[:2])
    invalid = proposal("Broken", 2) | {"legs": []}
    designer = Designer(
        [proposal("Idea A", 2), invalid, proposal("Copy of A", 2)],
        [proposal("Idea B", 3)],
    )
    lab = fresh(lab_dirs, tmp_path, designer)
    outcome = lab.run_generate(NOW, rounds=2, per_round=2, guidance="prefer spreads")
    assert len(outcome["created"]) == 2
    assert len(outcome["dropped"]) == 2
    created = [lab.spec(i) for i in outcome["created"]]
    assert all(s.source == "ai" and lab.result(s) is not None for s in created)

    first, second = designer.prompts[0][1], designer.prompts[1][1]
    assert first["owner_guidance"] == "prefer spreads"
    assert first["account"]["max_loss_per_trade_inr"] == 5000
    # Baselines were tested first, and round two sees round one's training results.
    assert len(first["already_tested"]) == 2
    assert all("training_result" in row for row in first["already_tested"])
    names = [row["name"] for row in second["already_tested"]]
    assert "Idea A" in names
    text = json.dumps(second)
    assert "training_result" in text and '"test"' not in text and "test_2x" not in text
    assert "TRAINING period only" in SYSTEM and "not giving investment advice" in SYSTEM


def test_feedback_never_contains_test_period_figures():
    result = {"train": {"trades": 3, "net_pnl": 1.0, "blocked": {}}, "test": {"net_pnl": 777.0}}
    assert "777" not in json.dumps(feedback_row(spec(), result))


def test_ai_failure_is_reported_and_keeps_earlier_work(lab_dirs, tmp_path, monkeypatch):
    monkeypatch.setattr("algoverse.lab.service.TEMPLATES", TEMPLATES[:1])
    designer = Designer([proposal("Idea A", 2)], LLMError("offline"))
    lab = fresh(lab_dirs, tmp_path, designer)
    with pytest.raises(LabError, match="round 2"):
        lab.run_generate(NOW, rounds=2, per_round=1)
    assert any(s.name == "Idea A" for s in lab.specs())


def test_api(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, ai_enabled=False)
    client = TestClient(create_app(settings))
    body = client.get("/api/lab").json()
    assert len(body["strategies"]) == 10 and body["job"] is None and not body["history"]["ready"]
    assert client.post("/api/lab/strategies", json=SPREAD).status_code == 403
    saved = client.post("/api/lab/strategies", json=SPREAD, headers=HEADER).json()
    assert saved["source"] == "user"
    bad = client.post("/api/lab/strategies", json={**SPREAD, "legs": []}, headers=HEADER)
    assert bad.status_code == 400
    assert client.post("/api/lab/backtest", json={}, headers=HEADER).status_code == 400
    assert client.post("/api/lab/generate", json={}, headers=HEADER).status_code == 400
    assert client.delete(f"/api/lab/strategies/{saved['id']}", headers=HEADER).status_code == 200
    assert client.delete("/api/lab/strategies/tpl_long_call", headers=HEADER).status_code == 400


def test_lab_cannot_reach_order_placement():
    from pathlib import Path

    import algoverse.lab as package

    source = "".join(p.read_text() for p in Path(package.__file__).parent.glob("*.py"))
    assert "upstox" not in source.lower() and "/order" not in source
    assert Services is not None and EventCalendar is not None and date is not None


def test_a_stored_copy_of_a_template_is_listed_once(lab_dirs, tmp_path):
    lab = fresh(lab_dirs, tmp_path)
    template = TEMPLATES[0]
    folder = tmp_path / "data" / "lab" / "strategies"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{template.id}.json").write_text(template.model_dump_json())
    ids = [spec.id for spec in lab.specs()]
    assert ids.count(template.id) == 1

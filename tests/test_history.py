from datetime import timedelta

import pytest
from algoverse.api.app import create_app
from algoverse.lab.history import _readable
from algoverse.lab.service import LabError
from algoverse.settings import Settings
from fastapi.testclient import TestClient
from test_lab import NOW, SPREAD, fresh, lab_dirs  # noqa: F401


def test_every_lab_test_is_kept_with_the_rules_it_tested(lab_dirs, tmp_path):  # noqa: F811
    lab = fresh(lab_dirs, tmp_path)
    saved = lab.save(SPREAD, NOW)
    lab.run_backtest([saved.id], NOW)
    edited = lab.save({**SPREAD, "id": saved.id, "exits": {"target_frac": 0.9, "exit_dte": 1}}, NOW)
    lab.run_backtest([edited.id], NOW + timedelta(minutes=5))

    history = lab.test_history(kind="lab")
    newest, oldest = history["runs"]
    assert history["total"] == 2 and history["summary"]["by_kind"][0]["runs"] == 2
    assert newest["strategy_name"] == SPREAD["name"] and not newest["changed_since"]
    assert oldest["changed_since"] and oldest["exists"]
    assert [p["label"] for p in newest["periods"]] == ["Training", "Test (unseen)"]
    assert newest["verdict"] and newest["entry_id"]
    assert history["test_period"]["reads"] == 2

    old = lab.test_history_entry(oldest["entry_id"])
    assert not old["same_rules"] and old["entry"]["spec"]["exits"]["target_frac"] != 0.9
    assert old["latest"]["evaluated_at"] == (NOW + timedelta(minutes=5)).isoformat()
    assert lab.test_history_entry(newest["entry_id"])["same_rules"]

    assert lab.test_history(query="nothing like this")["total"] == 0
    assert lab.test_history(query=SPREAD["name"][:5].lower())["total"] == 2
    with pytest.raises(LabError):
        lab.test_history_entry("../escape")


def test_names_for_strategies_no_longer_stored():
    assert _readable("long_otm_put_tuned_1_e34a6b") == "Long otm put (tuned, round 1)"
    assert _readable("tpl_call_butterfly") == "Call butterfly"


def test_api(tmp_path):
    client = TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path, ai_enabled=False)))
    body = client.get("/api/lab/history").json()
    assert body["total"] == 0 and body["runs"] == [] and body["test_period"] is None
    assert client.get("/api/lab/history/20260101-000000-missing").status_code == 400

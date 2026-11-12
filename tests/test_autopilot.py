import threading
from types import SimpleNamespace

import pytest
from algoverse.api.app import create_app
from algoverse.lab.evaluate import FAILED, HELD_UP, TOO_FEW
from algoverse.lab.seeds import TEMPLATES
from algoverse.lab.service import LabError, LabService, _write
from algoverse.lab.spec import StrategySpec
from algoverse.settings import Settings
from fastapi.testclient import TestClient
from test_lab import HEADER, NOW, SPREAD, Designer, fresh, lab_dirs, proposal  # noqa: F401


def make(lab, name, offset, verdict, trades=20, sharpe=1.0):
    spec = StrategySpec.model_validate(
        {**SPREAD, "id": "ai_" + name.lower().replace(" ", "_"), "name": name, "source": "ai"}
        | {"legs": [SPREAD["legs"][0], {**SPREAD["legs"][1], "value": offset}]}
    )
    _write(lab._spec_path(spec.id), spec.model_dump(mode="json"))
    from algoverse.lab.spec import signature

    _write(
        lab._result_path(spec.id),
        {
            "signature": signature(spec),
            "verdict": verdict,
            "train": {"trades": trades, "sharpe": sharpe, "net_pnl": 100.0},
            "test": {"sharpe": 0.5},
        },
    )
    return spec.id


@pytest.fixture
def pilot(tmp_path, monkeypatch):
    """A lab whose design and tuning steps are scripted, so only the loop is tested."""
    lab = LabService(tmp_path, tmp_path / "config")
    config = SimpleNamespace(lab=SimpleNamespace(min_test_trades=10))
    monkeypatch.setattr(lab, "_require_history", lambda: None)
    monkeypatch.setattr(lab, "_tuning_windows", lambda: (config, None))
    lab.designs = []
    lab.tuned = []

    def generate(now, rounds, per_round, guidance, note, progress):
        step = lab.designs.pop(0)
        if isinstance(step, Exception):
            raise step
        return {"created": [make(lab, *row) for row in step], "dropped": ["x: dropped"]}

    def tune(start_id, now, rounds, guidance, note, progress):
        lab.tuned.append(start_id)
        final = make(lab, f"{start_id} tuned", 5, HELD_UP)
        return {"id": f"session-{start_id}", "final": {"id": final, "verdict": HELD_UP}}

    monkeypatch.setattr(lab, "run_generate", generate)
    monkeypatch.setattr(lab, "run_tuning", tune)
    return lab


def run(lab, cycles=2, stop=None):
    return lab.run_autopilot(NOW, cycles, 8, 3, 2, "", stop or threading.Event())


def test_each_cycle_tunes_the_best_training_design_and_archives_failures(pilot):
    pilot.designs = [
        [("Low", 2, FAILED, 20, 0.2), ("High", 3, FAILED, 20, 1.5), ("Thin", 4, TOO_FEW, 2, 9.0)],
        [("Keeper", 6, HELD_UP, 20, 0.1)],
    ]
    state = run(pilot)
    first, second = state["cycles"]
    # Thin had the best Sharpe but too few trades, so High was tuned.
    assert first["picked"]["name"] == "High" and pilot.tuned[0] == "ai_high"
    assert first["session_id"] == "session-ai_high" and first["final"]["verdict"] == HELD_UP
    assert first["dropped"] == 1 and first["archived"] == 3
    assert [k["name"] for k in first["kept"]] == ["ai_high tuned"]
    assert {k["name"] for k in second["kept"]} == {"Keeper", "ai_keeper tuned"}
    names = {spec.name for spec in pilot.specs()} - {t.name for t in TEMPLATES}
    assert names == {"ai_high tuned", "Keeper", "ai_keeper tuned"}
    # Archived, not deleted, and still known to the designer.
    assert {spec.name for spec, _ in pilot._archived()} == {"Low", "High", "Thin"}
    assert state["ended"] == "Finished all 2 cycles" and not state["running"]
    assert pilot.autopilot()["cycles"][1]["number"] == 2


def test_no_tuning_when_no_design_has_enough_trades(pilot):
    pilot.designs = [[("Thin", 2, TOO_FEW, 1, 1.0)]]
    cycle = run(pilot, cycles=1)["cycles"][0]
    assert cycle["picked"] is None and not pilot.tuned
    assert "enough training trades" in cycle["message"]


def test_stops_on_request_and_after_repeated_ai_failures(pilot):
    stop = threading.Event()
    stop.set()
    assert run(pilot, stop=stop)["ended"] == "Stopped on request"
    pilot.designs = [LabError("offline")] * 3
    state = run(pilot, cycles=5)
    assert len(state["cycles"]) == 3 and "3 cycles in a row" in state["ended"]
    assert state["cycles"][0]["message"] == "offline"


def test_a_run_that_died_shows_as_interrupted(pilot):
    _write(pilot._autopilot_path(), {"running": True, "ended": None, "cycles": []})
    assert pilot.autopilot()["running"] is False
    assert "Interrupted" in pilot.autopilot()["ended"]


def test_designer_still_sees_archived_strategies(lab_dirs, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr("algoverse.lab.service.TEMPLATES", TEMPLATES[:1])
    designer = Designer([proposal("Idea A", 2)], [proposal("Idea A again", 2)])
    lab = fresh(lab_dirs, tmp_path, designer)
    created = lab.run_generate(NOW, rounds=1, per_round=1)["created"]
    lab._archive(created[0])
    assert lab.spec(created[0]) is None
    outcome = lab.run_generate(NOW, rounds=1, per_round=1)
    names = [row["name"] for row in designer.prompts[1][1]["already_tested"]]
    assert "Idea A" in names
    # Same rules as the archived one, so it is dropped rather than tested again.
    assert outcome["created"] == [] and "same rules" in outcome["dropped"][0]


def test_api(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, ai_enabled=False)
    client = TestClient(create_app(settings))
    body = client.get("/api/lab").json()
    assert body["autopilot"] is None and body["archived"] == 0
    assert client.post("/api/lab/autopilot", json={}).status_code == 403
    assert (
        client.post("/api/lab/autopilot", json={"cycles": 500}, headers=HEADER).status_code == 422
    )
    # No history stored yet.
    assert client.post("/api/lab/autopilot", json={}, headers=HEADER).status_code == 400
    stop = client.post("/api/lab/autopilot/stop", headers=HEADER)
    assert stop.status_code == 400 and "not running" in stop.json()["detail"]

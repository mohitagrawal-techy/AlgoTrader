"""Tuning a strategy one setting at a time, as a tree of tested changes."""

import json

import pytest
from algoverse.ai.client import LLMError, LLMResult
from algoverse.api.app import create_app
from algoverse.lab.ladder import ROOTS
from algoverse.lab.service import LabError
from algoverse.lab.spec import StrategySpec
from algoverse.lab.tuner import Change, apply_change, built_in_changes, public
from algoverse.settings import Settings
from fastapi.testclient import TestClient
from test_lab import HEADER, NOW
from test_ladder import fresh, ladder_dirs  # noqa: F401

CALL = ROOTS[0]
EMA_RULE = {
    "type": "compare",
    "timeframe": "1d",
    "left": {"name": "close"},
    "op": "above",
    "right": {"name": "ema", "params": {"period": 20}},
}


class Tuner:
    model = "fake:1"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, system, user, schema):
        self.prompts.append((system, json.loads(user)))
        reply = self.replies.pop(0) if self.replies else LLMError("no more replies")
        if isinstance(reply, Exception):
            raise reply
        return LLMResult(json.dumps(reply), 10, 10)


def reply(*changes, analysis="Stops are hit too often."):
    return {
        "analysis": analysis,
        "suggestions": [{"path": p, "value": v, "reason": "because"} for p, v in changes],
    }


# ---- Applying one change ------------------------------------------------------------------


def test_one_setting_changes():
    child, label = apply_change(CALL, Change("exits.stop_frac", 0.3))
    assert child.exits.stop_frac == 0.3 and label == "exits.stop_frac: 0.4 → 0.3"
    child, _ = apply_change(CALL, Change("legs.0.value", 0.3))
    assert child.legs[0].value == 0.3
    child, _ = apply_change(CALL, Change("entry.trend", ["up"]))
    assert child.entry.trend == ["up"]


def test_lists_grow_and_shrink_by_one_item():
    child, label = apply_change(CALL, Change("entry.rules.+", EMA_RULE))
    assert len(child.entry.rules) == 1 and label.startswith("add to entry.rules")
    grown, _ = apply_change(child, Change("entry.rules.0.right.params.period", 50))
    assert grown.entry.rules[0].right.params["period"] == 50
    shrunk, label = apply_change(child, Change("entry.rules.0", None))
    assert shrunk.entry.rules == [] and label == "remove entry.rules.0"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (Change("name", "x"), "only legs, entry, exits"),
        (Change("exits.nonsense", 1), "no such setting"),
        (Change("legs.5.value", 0.2), "no such setting"),
        (Change("exits.stop_frac", 0.4), "already 0.4"),
        (Change("exits.stop_frac", 50), "stop_frac"),
        (Change("entry.dte_min", 50), "entry.dte_min"),
        (Change("exits.target_unit.+", 1), "items can be added only"),
    ],
)
def test_bad_changes_are_refused(change, message):
    child, reason = apply_change(CALL, change)
    assert child is None and message in reason


def test_built_in_changes_are_mostly_valid():
    spec = apply_change(CALL, Change("entry.rules.+", EMA_RULE))[0]
    changes = built_in_changes(spec)
    valid = [c for c in changes if apply_change(spec, c)[0] is not None]
    assert len(valid) >= 6
    assert any(c.path == "entry.rules.0.right.params.period" for c in valid)


# ---- A tuning run ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tuned(ladder_dirs, tmp_path_factory):  # noqa: F811
    ai = Tuner(
        reply(
            ("name", "renamed"),  # refused: not a trading setting
            ("exits.stop_frac", 0.3),
            ("exits.target_frac", 1.0),
        ),
        reply(("legs.0.value", 0.3), ("exits.exit_dte", 0)),
    )
    lab = fresh(ladder_dirs, tmp_path_factory.mktemp("tune"))
    lab._client = ai
    notes = []
    session = lab.run_tuning(
        "tpl_long_call", NOW, rounds=2, per_round=2, note=notes.append, new_id="t1"
    )
    return lab, session, ai, notes


def test_tree_holds_the_root_and_each_tested_change(tuned):
    _, session, _, notes = tuned
    nodes = session["nodes"]
    root = nodes[session["root_id"]]
    assert root["depth"] == 0 and root["accepted"] is None
    first = session["rounds"][0]
    assert first["source"] == "ai" and first["analysis"] == "Stops are hit too often."
    assert len(first["tried"]) == 2
    assert any("only legs, entry, exits" in s for s in first["skipped"])
    for node_id in first["tried"]:
        node = nodes[node_id]
        assert node["parent_id"] == root["id"] and node["depth"] == 1
        assert node["accepted"] in (True, False) and node["verdict"]
        assert node["reason"] == "because"
    assert notes[0].startswith("Testing the starting version")
    json.dumps(public(session), allow_nan=False)


def test_ai_never_sees_unseen_session_results(tuned):
    _, _, ai, _ = tuned
    for _, user in ai.prompts:
        text = json.dumps(user)
        assert "validate" not in text
        assert set(user["design_results"]) >= {"trades", "net_pnl", "exit_reasons"}
    if len(ai.prompts) > 1:
        assert {"change", "kept"} <= set(ai.prompts[1][1]["already_tried"][0])


def test_best_version_gets_the_final_test(tuned):
    lab, session, _, _ = tuned
    best = session["nodes"][session["best_id"]]
    if best["depth"] == 0:
        assert "final" not in session
    else:
        assert session["final"]["id"] == best["id"] and lab.spec(best["id"]) is not None
    view = lab.tuning_session("t1")
    assert "validate_daily" not in json.dumps(view)
    root = view["nodes"][view["root_id"]]
    days = [point["day"] for point in root["validate_curve"]]
    assert days == sorted(days)
    assert root["validate_curve"][-1]["pnl"] == pytest.approx(root["validate"]["net_pnl"], abs=1)


def test_overview_lists_sessions(tuned):
    lab, session, _, _ = tuned
    view = lab.tuning_overview()
    assert view["ready"] is True
    row = view["sessions"][0]
    assert row["id"] == "t1" and row["tested"] == len(session["nodes"]) - 1
    assert any(s["id"] == "tpl_long_call" for s in view["strategies"])


def test_continue_from_a_node_and_save_it(tuned):
    lab, session, _, _ = tuned
    child_id = session["rounds"][0]["tried"][0]
    lab._client = Tuner(reply(("exits.target_frac", 0.8)))
    grown = lab.run_tuning(None, NOW, session_id="t1", node_id=child_id, rounds=1, per_round=1)
    last = grown["rounds"][-1]
    assert last["parent_id"] == child_id and len(last["tried"]) == 1
    assert grown["nodes"][last["tried"][0]]["depth"] == 2
    saved = lab.save_tuned("t1", last["tried"][0], NOW)
    assert lab.spec(saved) is not None
    with pytest.raises(LabError):
        lab.save_tuned("t1", "missing", NOW)


def test_ai_failure_falls_back_to_built_in_changes(ladder_dirs, tmp_path):  # noqa: F811
    lab = fresh(ladder_dirs, tmp_path)
    lab._client = Tuner(LLMError("down"))
    notes = []
    session = lab.run_tuning(
        "tpl_long_call", NOW, rounds=1, per_round=1, note=notes.append, new_id="t2"
    )
    assert session["rounds"][0]["source"] == "built_in"
    assert any("AI model failed" in note for note in notes)


def test_tuning_without_ai(ladder_dirs, tmp_path):  # noqa: F811
    lab = fresh(ladder_dirs, tmp_path)
    session = lab.run_tuning("tpl_long_call", NOW, rounds=1, per_round=1, use_ai=False, new_id="t3")
    assert session["rounds"][0]["source"] == "built_in"
    assert len(session["rounds"][0]["tried"]) == 1


def test_bad_requests(ladder_dirs, tmp_path):  # noqa: F811
    lab = fresh(ladder_dirs, tmp_path)
    with pytest.raises(LabError):
        lab.start_tuning("missing", NOW, use_ai=False)
    with pytest.raises(LabError):
        lab.tuning_session("../etc")
    with pytest.raises(LabError):
        lab.tuning_session("nope")


def test_api(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, ai_enabled=False)
    client = TestClient(create_app(settings))
    assert client.get("/api/lab/tune").json()["sessions"] == []
    assert client.post("/api/lab/tune", json={"start_id": "x"}).status_code == 403
    response = client.post("/api/lab/tune", json={"start_id": "x"}, headers=HEADER)
    assert response.status_code == 400 and "history" in response.json()["detail"]
    assert client.get("/api/lab/tune/nope").status_code == 400
    # Up to 15 rounds pass validation; this store simply has no history yet.
    allowed = client.post("/api/lab/tune", json={"start_id": "x", "rounds": 15}, headers=HEADER)
    assert allowed.status_code == 400
    too_many = client.post(
        "/api/lab/tune", json={"start_id": "x", "rounds": 16, "per_round": 11}, headers=HEADER
    )
    assert too_many.status_code == 422
    assert {item["loc"][-1] for item in too_many.json()["detail"]} == {"rounds", "per_round"}
    save = client.post("/api/lab/tune/nope/save", json={"node_id": "a"}, headers=HEADER)
    assert save.status_code == 400


def test_spec_round_trip():
    child, _ = apply_change(CALL, Change("exits.trail", 0.2))
    assert StrategySpec.model_validate(child.model_dump()).exits.trail == 0.2

"""Tuning a strategy one setting at a time, as a tree of tested changes."""

import json

import pytest
from algoverse.ai.client import LLMError, LLMResult
from algoverse.api.app import create_app
from algoverse.lab.ladder import ROOTS
from algoverse.lab.service import LabError
from algoverse.lab.spec import StrategySpec
from algoverse.lab.tune_params import allows, tunable_params
from algoverse.lab.tuner import Change, apply_change, built_in_changes, public, trade_timing
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


# ---- Reading the model's replies ------------------------------------------------------------

# Two replies gpt-oss:20b gave on 12 Nov 2026: suggestions without brackets, and no analysis.
BARE = (
    '{"path":"exits.stop_frac","value":0.2,"reason":"Fewer premature stops."},'
    '{"path":"entry.max_trades_per_day","value":2,"reason":"More entries."}'
)
NO_ANALYSIS = '{"suggestions":[{"path":"exits.target_frac","value":1.5,"reason":"Bank sooner."}]}'


def test_lenient_reading_of_small_model_replies():
    from algoverse.lab.tuner import parse_tuning

    bare = parse_tuning(BARE)
    assert [s.path for s in bare.suggestions] == ["exits.stop_frac", "entry.max_trades_per_day"]
    assert bare.analysis == ""
    assert parse_tuning(NO_ANALYSIS).suggestions[0].value == 1.5
    fenced = parse_tuning(f"```json\n{json.dumps(reply(('exits.trail', 0.2)))}\n```")
    assert fenced.analysis and fenced.suggestions[0].path == "exits.trail"
    lines = parse_tuning(BARE.replace("},{", "}\n{"))
    assert len(lines.suggestions) == 2
    bullets = parse_tuning("- " + BARE.replace("},{", "}\n- {"))
    assert len(bullets.suggestions) == 2
    numbered = parse_tuning("1. " + BARE.replace("},{", "}\n2) {"))
    assert len(numbered.suggestions) == 2
    single = parse_tuning('{"path": "exits.trail", "value": 0.2}')
    assert single.suggestions[0].path == "exits.trail"
    for bad in ("I think you should widen the stop.", "{}", '{"suggestions": []}', "", "  "):
        with pytest.raises(ValueError):
            parse_tuning(bad)


class Raw:
    """Returns replies as written, so unreadable text can be tested."""

    model = "fake:1"

    def __init__(self, *texts):
        self.texts = list(texts)
        self.prompts = []

    def complete(self, system, user, schema):
        self.prompts.append(user)
        return LLMResult(self.texts.pop(0), 10, 10)


def suggest(client, tmp_path):
    from algoverse.ai.budget import AiLog
    from algoverse.lab.tuner import ai_changes

    return ai_changes(client, AiLog(tmp_path / "ai.sqlite"), NOW, CALL, {}, [], 4, {}, "", "")


def test_an_unreadable_reply_is_asked_for_again(tmp_path):
    from algoverse.lab.tuner import NO_ANALYSIS as PLACEHOLDER
    from algoverse.lab.tuner import REMINDER

    client = Raw("Sure! Here are my ideas.", BARE)
    found = suggest(client, tmp_path)
    assert found.source == "ai" and len(found.changes) == 2
    assert found.analysis == PLACEHOLDER
    assert REMINDER in client.prompts[1] and REMINDER not in client.prompts[0]


def test_repeated_unreadable_replies_fall_back(tmp_path):
    with pytest.raises(LLMError, match="3 times"):
        suggest(Raw("no", "", "still no"), tmp_path)


def test_every_round_tests_exactly_the_changes_asked_for(ladder_dirs, tmp_path):  # noqa: F811
    lab = fresh(ladder_dirs, tmp_path)
    # One usable suggestion, then only repeats: the AI is asked again, twice, and
    # built-in nudges fill the rest of the round.
    ai = Tuner(
        reply(("exits.stop_frac", 0.3), ("name", "renamed")),
        reply(("exits.stop_frac", 0.3)),
        reply(("exits.stop_frac", 0.3)),
    )
    lab._client = ai
    session = lab.run_tuning("tpl_long_call", NOW, rounds=3, per_round=3, new_id="t4")
    assert [len(r["tried"]) for r in session["rounds"]] == [3, 3, 3]
    assert len(session["nodes"]) == 1 + 9
    first = session["rounds"][0]
    assert first["built_in_added"] == 2 and len(ai.prompts) >= 3
    # Repeats are named for the AI, so it can avoid them when asked again.
    assert any(t["kept"] is None for t in ai.prompts[1][1]["already_tried"])


def test_rounds_go_on_when_nothing_is_kept(ladder_dirs, tmp_path):  # noqa: F811
    lab = fresh(ladder_dirs, tmp_path)
    session = lab.run_tuning("tpl_long_call", NOW, rounds=4, per_round=5, use_ai=False, new_id="t5")
    assert [len(r["tried"]) for r in session["rounds"]] == [5, 5, 5, 5]
    ids = [i for r in session["rounds"] for i in r["tried"]]
    assert len(set(ids)) == 20
    for record in session["rounds"]:
        for node_id in record["tried"]:
            assert session["nodes"][node_id]["parent_id"] == record["parent_id"]


# ---- Choosing which settings to tune --------------------------------------------------------


def test_tunable_settings_list_the_strategy_numbers_and_rules():
    spec = apply_change(CALL, Change("entry.rules.+", EMA_RULE))[0]
    items = {item["path"]: item for item in tunable_params(spec)}
    assert items["exits.stop_frac"]["current"] == 0.4
    assert items["legs.0.value"]["group"] == "Legs"
    assert items["entry.rules.0"]["kind"] == "rule"
    # An indicator setting left at its default can still be chosen.
    assert items["entry.rules.0.right.params.period"]["current"] == 20
    assert "entry.rules.+" in items
    assert allows("entry.rules.0.right.params.period", ["entry.rules.0"])
    assert not allows("entry.rules.1", ["entry.rules.0"])
    assert allows("anything", None)


def test_tuning_changes_only_the_chosen_settings(ladder_dirs, tmp_path):  # noqa: F811
    lab = fresh(ladder_dirs, tmp_path)
    session = lab.run_tuning(
        "tpl_long_call",
        NOW,
        rounds=2,
        per_round=4,
        use_ai=False,
        new_id="t6",
        params=["exits.stop_frac"],
    )
    tried = [session["nodes"][i] for r in session["rounds"] for i in r["tried"]]
    assert len(tried) == 8 and session["params"] == ["exits.stop_frac"]
    assert {node["path"] for node in tried} == {"exits.stop_frac"}


def test_the_ai_is_told_the_chosen_settings(ladder_dirs, tmp_path):  # noqa: F811
    lab = fresh(ladder_dirs, tmp_path)
    ai = Tuner(reply(("legs.0.value", 0.3), ("exits.stop_frac", 0.3)))
    lab._client = ai
    session = lab.run_tuning(
        "tpl_long_call", NOW, rounds=1, per_round=1, new_id="t7", params=["exits.stop_frac"]
    )
    only = ai.prompts[0][1]["only_change"]
    assert [item["path"] for item in only] == ["exits.stop_frac"]
    first = session["rounds"][0]
    assert any("not among the settings chosen" in text for text in first["skipped"])
    assert session["nodes"][first["tried"][0]]["path"] == "exits.stop_frac"
    with pytest.raises(LabError):
        lab.start_tuning("tpl_long_call", NOW, use_ai=False, params=[])


def test_tuning_again_changes_only_the_newly_chosen_settings(ladder_dirs, tmp_path):  # noqa: F811
    lab = fresh(ladder_dirs, tmp_path)
    first = lab.run_tuning("tpl_long_call", NOW, rounds=1, per_round=2, use_ai=False, new_id="t8")
    assert first["params"] is None
    again = lab.run_tuning(
        None,
        NOW,
        session_id="t8",
        node_id=first["best_id"],
        rounds=1,
        per_round=3,
        use_ai=False,
        params=["exits.target_frac"],
    )
    latest = again["rounds"][-1]
    assert again["params"] == ["exits.target_frac"] and len(latest["tried"]) == 3
    assert {again["nodes"][i]["path"] for i in latest["tried"]} == {"exits.target_frac"}


def test_trade_timing_groups_by_weekday_and_entry_hour():
    cells = trade_timing(
        [
            {"opened_at": "2026-04-20T09:50:00+05:30", "net_pnl": -100.0},
            {"opened_at": "2026-04-20T09:55:00+05:30", "net_pnl": 300.0},
            {"opened_at": "2026-04-21T04:30:00+00:00", "net_pnl": 50.0},
            {"opened": "2026-04-24", "net_pnl": -20.0},
        ]
    )
    assert cells == [
        {"day": 0, "hour": 9, "trades": 2, "wins": 1, "pnl": 200.0},
        # 04:30 UTC is 10:00 in India.
        {"day": 1, "hour": 10, "trades": 1, "wins": 1, "pnl": 50.0},
        {"day": 4, "hour": -1, "trades": 1, "wins": 0, "pnl": -20.0},
    ]


def test_each_tested_version_keeps_its_timing(tuned):
    _, session, _, _ = tuned
    for node in session["nodes"].values():
        for key in ("design_timing", "validate_timing"):
            cells = node[key]
            assert isinstance(cells, list)
            assert sum(c["trades"] for c in cells) == node[key.split("_")[0]]["trades"]

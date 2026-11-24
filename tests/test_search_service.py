"""Parameter search, dossiers and regimes through the lab service and API, plus sizing."""

import json
import time
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest
from algoverse.ai.client import LLMResult
from algoverse.api.app import create_app
from algoverse.lab.seeds import TEMPLATES
from algoverse.lab.service import LabError
from algoverse.lab.spec import Budget, CustomSet
from algoverse.settings import Settings
from fastapi.testclient import TestClient
from test_lab import HEADER, NOW
from test_ladder import fresh, ladder_dirs  # noqa: F401


@pytest.fixture(scope="module")
def searched(ladder_dirs, tmp_path_factory):  # noqa: F811
    lab = fresh(ladder_dirs, tmp_path_factory.mktemp("search"))
    notes = []
    record = lab.run_search(
        "tpl_long_call", ["exits.stop_frac"], "random", 4, NOW, notes.append, search_id="s1"
    )
    return lab, record, notes


def test_search_logs_every_setting_and_maps_its_neighbours(searched):
    lab, record, notes = searched
    assert record["state"] == "done" and len(record["trials"]) == 4
    assert record["trials"][0]["values"]["exits.stop_frac"] == pytest.approx(
        next(s for s in TEMPLATES if s.id == "tpl_long_call").exits.stop_frac
    )
    assert record["validated"] and record["sensitivity"]["xs"]
    assert record["sensitivity"]["y"] is None
    kinds = {trial["kind"] for trial in lab_trials(lab)}
    assert {"search_design", "search_validate"} <= kinds
    assert any(note.startswith("Searched") for note in notes)
    json.dumps(lab.search("s1"), allow_nan=False)


def lab_trials(lab):
    from algoverse.backtest.registry import Registry

    return Registry(lab.data_dir).all_trials()


def test_a_chosen_setting_is_saved_tested_and_graded_for_robustness(searched):
    lab, record, _ = searched
    if record["chosen_id"] is None:
        assert record["message"].startswith("No searched setting")
        return
    page = lab.backtest_detail(record["chosen_id"])
    assert page["search"]["id"] == "s1"
    keys = [check["key"] for check in page["stage"]["checks"]]
    assert "robust" in keys and "deflated" in keys


def test_search_space_lists_settings_and_past_searches(searched):
    lab, _, _ = searched
    space = lab.search_space("tpl_long_call")
    assert any(p["path"] == "exits.stop_frac" for p in space["params"])
    assert space["methods"] == ["grid", "random", "bayesian"]
    assert [s["id"] for s in space["searches"]] == ["s1"]


def test_search_refuses_unknown_settings_and_paths(ladder_dirs, tmp_path):  # noqa: F811
    lab = fresh(ladder_dirs, tmp_path)
    with pytest.raises(LabError):
        lab.run_search("tpl_long_call", ["name"], "random", 4, NOW)
    with pytest.raises(LabError):
        lab.run_search("tpl_long_call", ["exits.stop_frac"], "annealing", 4, NOW)
    with pytest.raises(LabError):
        lab.search("../x")


def test_backtest_page_and_overview_show_regimes(searched):
    lab, _, _ = searched
    lab.run_backtest(["tpl_long_call"], NOW)
    page = lab.backtest_detail("tpl_long_call")
    regimes = page["regimes"]
    assert {row["group"] for row in regimes["rows"]} == {"trend", "volatility", "move"}
    traded = sum(r["trades"] for r in regimes["rows"] if r["group"] == "trend")
    assert traded + regimes["unlabelled_trades"] >= page["result"]["train"]["trades"]
    row = next(r for r in lab.overview()["strategies"] if r["spec"]["id"] == "tpl_long_call")
    assert set(row["regimes"]) == {"suits", "avoid"}
    assert page["stage"]["significance"] is None or "dsr" in page["stage"]["significance"]


class Writer:
    model = "fake:1"

    def complete(self, system, user, schema):
        facts = json.loads(user)
        assert "Performance" in facts and "Hypothesis" not in facts
        return LLMResult(
            json.dumps(
                {
                    "hypothesis": "Calls gain when the trend holds.",
                    "weaknesses": ["Few trades."],
                    "next_steps": ["Test on more history."],
                }
            ),
            5,
            5,
        )


def test_dossier_notes_are_written_once_and_go_stale_with_a_new_result(searched):
    lab, _, _ = searched
    lab.run_backtest(["tpl_long_call"], NOW)
    before = lab.dossier("tpl_long_call")
    assert len(before["sections"]) == 15 and before["notes_written_at"] is None
    lab._client = Writer()
    job = lab.start_dossier_notes("tpl_long_call", NOW)
    for _ in range(600):
        if job["state"] != "running":
            break
        time.sleep(0.05)
    assert job["state"] == "done", job["message"]
    after = lab.dossier("tpl_long_call")
    hypothesis = next(s for s in after["sections"] if s["title"] == "Hypothesis")
    assert hypothesis["text"] == "Calls gain when the trend holds."
    name, text = lab.dossier_markdown("tpl_long_call")
    assert name == "tpl_long_call-dossier.md" and "Calls gain" in text
    lab.run_backtest(["tpl_long_call"], NOW.replace(hour=13))
    stale = lab.dossier("tpl_long_call")
    assert stale["notes_written_at"] is None


def test_dossier_needs_a_backtest_first(ladder_dirs, tmp_path):  # noqa: F811
    lab = fresh(ladder_dirs, tmp_path)
    lab._client = Writer()
    with pytest.raises(LabError, match="Backtest"):
        lab.start_dossier_notes("tpl_long_call", NOW)
    assert lab.dossier("tpl_long_call")["tested"] is False


def test_api(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, ai_enabled=False)
    client = TestClient(create_app(settings))
    space = client.get("/api/lab/strategies/tpl_long_call/search").json()
    assert space["params"] and space["searches"] == []
    body = {"start_id": "tpl_long_call", "params": ["exits.stop_frac"]}
    assert client.post("/api/lab/search", json=body).status_code == 403
    response = client.post("/api/lab/search", json=body, headers=HEADER)
    assert response.status_code == 400 and "history" in response.json()["detail"]
    bad = client.post("/api/lab/search", json={**body, "method": "x"}, headers=HEADER)
    assert bad.status_code == 422
    assert client.get("/api/lab/search/nope").status_code == 400
    dossier = client.get("/api/lab/strategies/tpl_long_call/dossier").json()
    assert len(dossier["sections"]) == 15
    text = client.get("/api/lab/strategies/tpl_long_call/dossier.md")
    assert text.status_code == 200 and "attachment" in text.headers["content-disposition"]
    assert text.text.startswith("# Research dossier")
    notes = client.post("/api/lab/strategies/tpl_long_call/dossier", headers=HEADER)
    assert notes.status_code == 400


# ---- Volatility sizing and circuit breakers in the risk engine and custom set --------------


def test_a_smaller_scale_sizes_fewer_lots(risk_config):
    from test_paper import candidate, evaluate

    full = evaluate(risk_config)
    half = evaluate(risk_config, cand=replace(candidate(), size_scale=0.5))
    assert full.lots == 5 and half.approved and half.lots == 2
    budget = risk_config.model_copy(update={"size_by_budget": True, "max_lots_per_trade": 1000})
    whole = evaluate(budget)
    part = evaluate(budget, cand=replace(candidate(), size_scale=0.5))
    assert part.lots == pytest.approx(whole.lots / 2, abs=1)


def test_circuit_breaker_blocks_new_trades_until_the_pause_ends():
    spec = TEMPLATES[0].model_copy(
        update={"budget": Budget(pause_after_losses=2, pause_sessions=3)}
    )

    def closed(day, pnl):
        return SimpleNamespace(
            strategy=spec.id,
            status="closed",
            created_at=f"{day}T10:00:00+05:30",
            closed_at=f"{day}T15:00:00+05:30",
            realized_pnl=pnl,
            exit_reason="stop",
        )

    ledger = SimpleNamespace(
        positions=lambda statuses: [closed("2026-03-02", -100.0), closed("2026-03-03", -50.0)]
    )
    custom = CustomSet([spec])
    custom.broker = SimpleNamespace(ledger=ledger)
    reasons = custom.limit_failures(spec, date(2026, 3, 5))
    assert reasons and reasons[0].startswith("circuit breaker: 2 losing trades")
    assert custom.limit_failures(spec, date(2026, 3, 9)) == []


class GoodSearcher:
    """Every setting trades and makes money; the closer the stop is to 0.8, the better."""

    def __init__(self, data_dir, config_dir, config, windows, now, progress=None):
        self.config = config
        self.runs = 0

    def _score(self, spec):
        sharpe = 2.0 - abs(spec.exits.stop_frac - 0.8)
        return {
            "trades": 30,
            "net_pnl": 1000.0,
            "sharpe": sharpe,
            "win_rate": 0.5,
            "max_drawdown": 100.0,
        }

    def design_scores(self, specs):
        self.runs += len(specs)
        return [self._score(spec) for spec in specs]

    validate_scores = design_scores


def test_the_chosen_setting_is_saved_and_given_the_final_test(
    ladder_dirs,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    import algoverse.lab.service as service

    monkeypatch.setattr(service, "Searcher", GoodSearcher)
    lab = fresh(ladder_dirs, tmp_path)
    record = lab.run_search("tpl_long_call", ["exits.stop_frac"], "grid", 5, NOW, search_id="g1")
    assert record["chosen_id"] and record["chosen_id"] != "tpl_long_call"
    assert record["sensitivity"]["robust"] and "final" in record
    chosen = lab.spec(record["chosen_id"])
    assert chosen.parent_id == "tpl_long_call" and chosen.step.startswith("search:")
    assert lab.result(chosen) is not None
    page = lab.backtest_detail(chosen.id)
    assert page["search"] == {
        "id": "g1",
        "method": "grid",
        "robust": True,
        "neighbour_share": record["sensitivity"]["neighbour_share"],
        "trials": 5,
    }
    assert "robust" in [check["key"] for check in page["stage"]["checks"]]

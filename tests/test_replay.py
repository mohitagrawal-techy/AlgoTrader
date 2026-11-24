import pytest
from algoverse.api.app import create_app
from algoverse.lab.replay import wall_seconds
from algoverse.lab.service import LabError
from algoverse.settings import Settings
from fastapi.testclient import TestClient
from test_lab import HEADER, SPREAD, fresh, lab_dirs  # noqa: F401

DRAFT = SPREAD | {"exits": {"target_frac": 0.3, "stop_frac": 0.3, "exit_dte": 1}}


def test_wall_seconds_are_ist_clock_time():
    assert wall_seconds("2026-11-12T09:15:00+05:30") % 86400 == 9 * 3600 + 15 * 60
    assert wall_seconds("2026-11-12T03:45:00+00:00") == wall_seconds("2026-11-12T09:15:00+05:30")


def test_replays_a_draft_trade_by_trade(lab_dirs, tmp_path):  # noqa: F811
    lab = fresh(lab_dirs, tmp_path)
    result = lab.replay(DRAFT, "NIFTY", 40)
    assert result["symbol"] == "NIFTY" and not result["intraday"]
    assert result["start"] <= result["end"]
    assert result["trades"], "the synthetic uptrend should trigger the bull spread"
    for trade in result["trades"]:
        assert trade["open_time"] <= trade["close_time"]
        assert {leg["side"] for leg in trade["legs"]} == {"buy", "sell"}
        times = [point["time"] for point in trade["path"]]
        assert times == sorted(times)
        assert all(trade["open_time"] < t < trade["close_time"] for t in times)
    # Nothing is saved: the draft is not in the lab afterwards.
    assert all(spec.name != SPREAD["name"] for spec in lab.specs() if spec.source == "user")


def test_replay_refuses_what_it_cannot_run(lab_dirs, tmp_path):  # noqa: F811
    lab = fresh(lab_dirs, tmp_path)
    with pytest.raises(LabError, match="option history"):
        lab.replay(DRAFT, "SENSEX", 20)
    with pytest.raises(LabError, match="not complete yet"):
        lab.replay(DRAFT | {"legs": []}, "NIFTY", 20)


def test_api_needs_the_dashboard(tmp_path):
    client = TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path, ai_enabled=False)))
    body = {"spec": DRAFT, "symbol": "NIFTY", "sessions": 21}
    assert client.post("/api/lab/replay", json=body).status_code == 403
    # No history stored yet.
    response = client.post("/api/lab/replay", json=body, headers=HEADER)
    assert response.status_code == 400 and "history" in response.json()["detail"]


def test_replays_a_saved_backtest_from_its_stored_trades(lab_dirs, tmp_path):  # noqa: F811
    from test_lab import NOW

    lab = fresh(lab_dirs, tmp_path)
    saved = lab.save(DRAFT | {"name": "Saved spread"}, NOW)
    result = lab.run_backtest([saved.id], NOW)[0]
    both = lab.backtest_replay(saved.id, "both", "NIFTY")
    stored = result["detail"]["train"]["trades"] + result["detail"]["test"]["trades"]
    assert both["stats"]["trades"] == len(stored) > 0
    assert both["start"] == str(result["windows"]["train_start"])
    assert both["end"] == str(result["windows"]["test_end"])
    test = lab.backtest_replay(saved.id, "test", "NIFTY")
    assert test["stats"]["trades"] == len(result["detail"]["test"]["trades"])
    assert [t["open_time"] for t in both["trades"]] == sorted(
        t["open_time"] for t in both["trades"]
    )
    with pytest.raises(LabError, match="no stored trades"):
        lab.backtest_replay(saved.id, "test_2x", "NIFTY")
    with pytest.raises(LabError, match="no longer exists"):
        lab.backtest_replay("missing", "test", "NIFTY")

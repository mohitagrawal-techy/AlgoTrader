"""Loading data from Upstox from the dashboard: jobs, their log, and the coverage summary."""

import threading
import time
from datetime import date

import pandas as pd
import pytest
from algoverse.api.app import create_app
from algoverse.brokers.base import AuthExpiredError
from algoverse.data.loader import ACTIONS, DataJobs
from algoverse.data.store import SnapshotStore
from algoverse.settings import Settings
from conftest import CONFIG_DIR, IST
from fastapi.testclient import TestClient
from test_lab import HEADER

SYMBOLS = ["NIFTY", "BANKNIFTY"]


def jobs(tmp_path, runner):
    found = DataJobs(tmp_path, CONFIG_DIR, SYMBOLS, {"INDIAVIX": "NSE_INDEX|India VIX"})
    found.runner = runner
    return found


def wait(job):
    for _ in range(200):
        if job["state"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_a_job_keeps_its_log_and_last_line(tmp_path):
    seen = {}

    def runner(key, start, out):
        seen.update(key=key, start=start)
        out("NIFTY 2026-10: 8000 candles")
        out("NIFTY: fetched 1 months")

    job = wait(jobs(tmp_path, runner).start("minutes", date(2026, 10, 1), True))
    assert job["state"] == "done" and job["message"] == "NIFTY: fetched 1 months"
    assert len(job["log"]) == 2 and job["log"][0].endswith("8000 candles")
    assert seen == {"key": "minutes", "start": date(2026, 10, 1)}


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (SystemExit("Upstox refused: needs Upstox Plus"), "Upstox refused: needs Upstox Plus"),
        (AuthExpiredError("expired"), "The Upstox session expired. Log in again and re-run."),
        (ValueError("boom"), "Failed: boom"),
    ],
)
def test_failures_are_explained(tmp_path, error, message):
    def runner(key, start, out):
        raise error

    job = wait(jobs(tmp_path, runner).start("expired", None, True))
    assert job["state"] == "failed" and job["message"] == message


def test_refusals(tmp_path):
    release = threading.Event()
    loader = jobs(tmp_path, lambda key, start, out: release.wait(2))
    with pytest.raises(ValueError, match="Log in"):
        loader.start("daily", None, False)
    with pytest.raises(ValueError, match="no start date"):
        loader.start("instruments", date(2026, 1, 1), True)
    with pytest.raises(ValueError, match="Unknown"):
        loader.start("delete_everything", None, True)
    running = loader.start("holidays", None, False)
    with pytest.raises(RuntimeError, match="already running"):
        loader.start("daily", None, True)
    release.set()
    assert wait(running)["state"] == "done"


def test_every_action_maps_to_a_command():
    from algoverse import cli

    for key in ACTIONS:
        assert key in (
            "instruments",
            "daily",
            "minutes",
            "expired",
            "fill_plan",
            "fill",
            "holidays",
        )
    for handler in (
        cli.cmd_instruments,
        cli.cmd_backfill,
        cli.cmd_backfill_minutes,
        cli.cmd_history_upstox,
        cli.cmd_history_fill,
        cli.cmd_holidays,
    ):
        assert "out" in handler.__code__.co_varnames


def test_inventory_reports_coverage(tmp_path):
    store = SnapshotStore(tmp_path)
    days = pd.bdate_range("2026-11-02", periods=5, tz=IST)
    store.upsert_candles(
        "NIFTY", pd.DataFrame({"ts": days, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0})
    )
    history = tmp_path / "history" / "fo"
    (history / "intraday" / "date=2026-11-03").mkdir(parents=True)
    pd.DataFrame({"a": [1]}).to_parquet(history / "2026-11-03.parquet")
    for name in ("nifty_upstox", "nifty_files", "banknifty_upstox"):
        pd.DataFrame({"a": [1]}).to_parquet(
            history / "intraday" / "date=2026-11-03" / f"{name}.parquet"
        )
    found = jobs(tmp_path, None).inventory()
    nifty = next(row for row in found["daily"] if row["name"] == "NIFTY")
    assert nifty["sessions"] == 5 and str(nifty["last"]) == "2026-11-06"
    assert next(r for r in found["daily"] if r["name"] == "BANKNIFTY")["sessions"] == 0
    intraday = {row["name"]: row["sessions"] for row in found["options_intraday"]}
    assert intraday == {"NIFTY": 1, "BANKNIFTY": 1}
    assert found["options_daily"]["sessions"] == 1
    assert {a["key"] for a in found["actions"]} == set(ACTIONS)


def test_api(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, ai_enabled=False)
    client = TestClient(create_app(settings))
    body = client.get("/api/data").json()
    assert body["session_valid"] is False and body["job"] is None
    assert client.post("/api/data/jobs", json={"action": "daily"}).status_code == 403
    refused = client.post("/api/data/jobs", json={"action": "daily"}, headers=HEADER)
    assert refused.status_code == 400 and "Log in" in refused.json()["detail"]
    assert client.get("/api/data/job").json() == {"job": None}

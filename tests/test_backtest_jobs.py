import shutil
import time
from datetime import date

import numpy as np
import pytest
from algoverse.backtest.jobs import BacktestBusy, BacktestError, BacktestJobs
from algoverse.backtest.registry import Registry
from conftest import CONFIG_DIR
from test_backtest import market_rows, save, weekdays


@pytest.fixture(scope="module")
def dirs(tmp_path_factory):
    root = tmp_path_factory.mktemp("jobs")
    config_dir = root / "config"
    shutil.copytree(CONFIG_DIR, config_dir)
    text = (config_dir / "backtest.yaml").read_text()
    text = (
        text.replace("symbols: [NIFTY, BANKNIFTY, FINNIFTY]", "symbols: [NIFTY]")
        .replace("warmup_sessions: 60", "warmup_sessions: 20")
        .replace("train_sessions: 252", "train_sessions: 30")
        .replace("test_sessions: 63", "test_sessions: 10")
        .replace("holdout_sessions: 63", "holdout_sessions: 10")
        .replace("workers: 8", "workers: 1")
        .replace("min_score: [50, 55, 60, 65, 70, 75]", "min_score: [50, 60]")
    )
    (config_dir / "backtest.yaml").write_text(text)
    data = root / "data"
    rng = np.random.default_rng(7)
    sessions = weekdays("2026-01-05", 80)
    save(data, market_rows(sessions, 23000 + np.cumsum(rng.normal(8, 100, len(sessions)))))
    return data, config_dir


def wait(jobs, limit=300):
    start = time.time()
    while jobs.job["state"] == "running" and time.time() - start < limit:
        time.sleep(0.2)
    return jobs.job


def test_options_describe_what_can_run(dirs):
    data, config_dir = dirs
    options = BacktestJobs(data, config_dir).options()
    assert options["walk_forward"]["ready"] and options["walk_forward"]["folds"] >= 1
    assert options["walk_forward"]["settings"] == 4
    assert options["single"]["min_scores"] == [50.0, 60.0] and options["single"]["profiles"]


def test_single_backtest_runs_in_the_background_and_saves_a_report(dirs, tmp_path):
    data, config_dir = dirs
    copy = tmp_path / "data"
    shutil.copytree(data, copy)
    jobs = BacktestJobs(copy, config_dir)
    first, last = jobs.options()["single"]["first"], jobs.options()["single"]["last"]
    started = jobs.start_single(first, last, 50, [])
    assert started["state"] == "running"
    with pytest.raises(BacktestBusy):
        jobs.start_single(first, last, 50, [])
    job = wait(jobs)
    assert job["state"] == "done", job["message"]
    reports = Registry(copy).list_reports()
    assert [r["id"] for r in reports] == [job["report_id"]]


def test_walk_forward_from_the_dashboard(dirs, tmp_path):
    data, config_dir = dirs
    copy = tmp_path / "data"
    shutil.copytree(data, copy)
    jobs = BacktestJobs(copy, config_dir)
    jobs.start_walk_forward()
    job = wait(jobs)
    assert job["state"] == "done", job["message"]
    assert Registry(copy).read_report(job["report_id"])["kind"] == "walk_forward"


@pytest.mark.parametrize(
    ("start", "end", "disabled"),
    [
        (date(2026, 3, 1), date(2026, 2, 1), []),
        (date(2025, 1, 1), date(2026, 2, 1), []),
        (None, None, ["no_such_profile"]),
    ],
)
def test_bad_requests_are_refused(dirs, start, end, disabled):
    data, config_dir = dirs
    jobs = BacktestJobs(data, config_dir)
    single = jobs.options()["single"]
    with pytest.raises(BacktestError):
        jobs.start_single(start or single["first"], end or single["last"], 50, disabled)


def test_walk_forward_needs_enough_history(tmp_path):
    with pytest.raises(BacktestError):
        BacktestJobs(tmp_path, CONFIG_DIR).start_walk_forward()

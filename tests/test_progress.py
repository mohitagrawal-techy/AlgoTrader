"""Progress reporting for long backtest jobs."""

from algoverse.backtest.config import load_backtest_config
from algoverse.backtest.dataview import History
from algoverse.backtest.progress import set_progress
from algoverse.lab.evaluate import evaluate_specs, run_tasks, split_windows
from test_lab import NOW, lab_dirs, spec  # noqa: F401


def test_stages_restart_when_the_total_changes():
    job = {}
    set_progress(job, 0, 10)
    first = job["progress"]["stage_started_at"]
    set_progress(job, 4, 10)
    assert job["progress"]["done"] == 4 and job["progress"]["stage_started_at"] == first
    set_progress(job, 12, 10)
    assert job["progress"]["done"] == 10
    set_progress(job, 1, 30)
    assert job["progress"]["total"] == 30 and job["progress"]["unit"] == "sessions"
    set_progress(job, 2, 5, "replays")
    assert job["progress"]["unit"] == "replays"


def test_evaluation_reports_sessions_replayed(lab_dirs):  # noqa: F811
    data, config_dir = lab_dirs
    calls = []
    evaluate_specs(data, config_dir, [spec()], NOW, on_sessions=lambda d, t: calls.append((d, t)))
    windows = split_windows(History(data).sessions, load_backtest_config(config_dir))
    # Training once, then the test period twice (normal and doubled spreads).
    expected = windows.train_sessions + 2 * windows.test_sessions
    assert calls[0] == (0, expected) and calls[-1] == (expected, expected)
    assert all(d <= t for d, t in calls)


def test_parallel_workers_share_their_counts(lab_dirs):  # noqa: F811
    data, config_dir = lab_dirs
    sessions = History(data).sessions
    windows = split_windows(sessions, load_backtest_config(config_dir))
    base = (str(data), str(config_dir), spec().model_dump_json())
    tasks = [
        (*base, windows.test_start, windows.test_end, 1.0),
        (*base, windows.test_start, windows.test_end, 2.0),
    ]
    calls = []
    results = run_tasks(tasks, 2, sessions, lambda d, t: calls.append((d, t)))
    assert len(results) == 2 and calls[-1] == (2 * windows.test_sessions,) * 2
    plain = run_tasks(tasks, 1)
    assert [r["stats"] for r in plain] == [r["stats"] for r in results]

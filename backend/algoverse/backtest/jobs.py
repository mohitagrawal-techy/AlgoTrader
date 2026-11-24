"""Backtests started from the dashboard, run one at a time in the background."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from algoverse.backtest.config import load_backtest_config
from algoverse.backtest.dataview import History
from algoverse.backtest.engine import ReplaySpec
from algoverse.backtest.progress import set_progress
from algoverse.backtest.walkforward import build_folds, run_backtest, run_walk_forward
from algoverse.evaluation.config import load_scoring_config

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class BacktestError(Exception):
    """The run cannot start. The message is safe to show."""


class BacktestBusy(BacktestError):
    """Another backtest is still running."""


class BacktestJobs:
    def __init__(self, data_dir: Path, config_dir: Path):
        self.data_dir = Path(data_dir)
        self.config_dir = Path(config_dir)
        self._lock = threading.Lock()
        self._job: dict | None = None

    @property
    def job(self) -> dict | None:
        return self._job

    def options(self) -> dict:
        """What the Run panel can offer, and whether a walk-forward has enough history."""
        config = load_backtest_config(self.config_dir)
        settings = config.walk_forward
        sessions = History(self.data_dir).sessions
        folds, _ = build_folds(sessions, config)
        needed = (
            config.warmup_sessions
            + settings.train_sessions
            + settings.test_sessions
            + settings.holdout_sessions
        )
        profiles = sorted(load_scoring_config(self.config_dir).profiles)
        return {
            "walk_forward": {
                "ready": bool(folds),
                "folds": len(folds),
                "needed": needed,
                "settings": len(config.grid.min_score) * len(config.grid.disabled_profiles),
                "train_sessions": settings.train_sessions,
                "test_sessions": settings.test_sessions,
                "holdout_sessions": settings.holdout_sessions,
            },
            "single": {
                "first": sessions[config.warmup_sessions]
                if len(sessions) > config.warmup_sessions
                else None,
                "last": sessions[-1] if sessions else None,
                "min_scores": sorted(set(config.grid.min_score)),
                "profiles": profiles,
            },
        }

    def _start(self, kind: str, work: Callable[[Callable[[str], None]], dict]) -> dict:
        with self._lock:
            if self._job and self._job["state"] == "running":
                raise BacktestBusy("A backtest is already running.")
            job = {
                "kind": kind,
                "state": "running",
                "message": "Starting",
                "started_at": datetime.now(IST).isoformat(),
                "finished_at": None,
                "report_id": None,
            }
            self._job = job

        def note(message: str, done: int | None = None, total: int | None = None) -> None:
            job["message"] = message
            if done is not None and total:
                set_progress(job, done, total, "replays")

        def run() -> None:
            try:
                report = work(note)
                job["report_id"] = report["id"]
                job.pop("progress", None)
                job["message"] = "Finished"
                job["state"] = "done"
            except (ValueError, BacktestError) as exc:
                job["state"], job["message"] = "failed", str(exc)
            except Exception:
                log.exception("Backtest job failed")
                job["state"], job["message"] = "failed", "The backtest failed. See the server log."
            job["finished_at"] = datetime.now(IST).isoformat()

        threading.Thread(target=run, daemon=True).start()
        return job

    def start_walk_forward(self) -> dict:
        if not self.options()["walk_forward"]["ready"]:
            raise BacktestError("Not enough history for a walk-forward yet.")
        return self._start(
            "walk_forward",
            lambda note: run_walk_forward(self.data_dir, self.config_dir, datetime.now(IST), note),
        )

    def start_single(self, start: date, end: date, min_score: float, disabled: list[str]) -> dict:
        options = self.options()["single"]
        if options["first"] is None:
            raise BacktestError("No imported history yet.")
        if start > end:
            raise BacktestError("The start must be on or before the end.")
        if start < options["first"] or end > options["last"]:
            raise BacktestError(
                f"Pick dates between {options['first']} and {options['last']}; earlier sessions "
                "are needed to warm up the indicators."
            )
        unknown = set(disabled) - set(options["profiles"])
        if unknown:
            raise BacktestError(f"Unknown strategy profile: {sorted(unknown)[0]}")
        spec = ReplaySpec(start, end, float(min_score), tuple(sorted(disabled)))
        return self._start(
            "backtest",
            lambda note: run_backtest(
                self.data_dir, self.config_dir, datetime.now(IST), spec, note=note
            ),
        )

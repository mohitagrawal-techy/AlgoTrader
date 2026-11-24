"""Progress of long backtest jobs, shared between worker processes and the job's page."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from multiprocessing import Manager
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
POLL_SECONDS = 2.0

# progress(done, total): how much of the current stage is finished, in sessions or replays.
Progress = Callable[[int, int], None]


def set_progress(job: dict, done: int, total: int, unit: str = "sessions") -> None:
    """Record progress on a job dict. A new stage starts when the total changes or work resets."""
    now = datetime.now(IST).isoformat()
    previous = job.get("progress")
    new_stage = (
        previous is None
        or previous.get("total") != total
        or previous.get("unit") != unit
        or done < previous.get("done", 0)
    )
    job["progress"] = {
        "done": min(done, total),
        "total": total,
        "unit": unit,
        "stage_started_at": now if new_stage else previous["stage_started_at"],
        "updated_at": now,
    }


class SessionCounter:
    """Counts sessions each replay has finished. Workers write their own count by task key."""

    def __init__(self, shared: bool):
        self._manager = Manager() if shared else None
        self.counts: Any = self._manager.dict() if self._manager else {}

    def total_done(self) -> int:
        try:
            return int(sum(self.counts.values()))
        except (OSError, EOFError):  # The manager is shutting down.
            return 0

    def close(self) -> None:
        if self._manager is not None:
            self._manager.shutdown()


class Poller:
    """Calls `progress` every couple of seconds from a background thread while work runs."""

    def __init__(self, counter: SessionCounter, total: int, progress: Progress):
        self._counter, self._total, self._progress = counter, total, progress
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.wait(POLL_SECONDS):
            self._progress(min(self._counter.total_done(), self._total), self._total)

    def __enter__(self) -> Poller:
        self._progress(0, self._total)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join()
        if exc[0] is None:
            self._progress(self._total, self._total)

"""Records every trial and report, so overfitting pressure is visible and holdouts stay locked."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from algoverse.dashboard import clean

SCHEMA = """
CREATE TABLE IF NOT EXISTS trials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    params TEXT NOT NULL,
    sharpe REAL,
    net_pnl REAL,
    trades INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS holdouts (
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    ts TEXT NOT NULL,
    report_id TEXT NOT NULL
);
"""
REPORT_ID = re.compile(r"^[a-z_]+-\d{8}-\d{6}-[0-9a-f]{6}$")


class Registry:
    def __init__(self, data_dir: Path):
        self.folder = Path(data_dir) / "backtests"
        self.reports = self.folder / "reports"
        self.reports.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "registry.sqlite"
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def record_trial(
        self,
        now: datetime,
        kind: str,
        config_hash: str,
        start: date,
        end: date,
        params: dict,
        sharpe: float | None,
        net_pnl: float | None,
        trades: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO trials (ts, kind, config_hash, window_start, window_end, params,"
                " sharpe, net_pnl, trades) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now.isoformat(),
                    kind,
                    config_hash,
                    start.isoformat(),
                    end.isoformat(),
                    json.dumps(params, sort_keys=True),
                    sharpe,
                    net_pnl,
                    trades,
                ),
            )

    def trial_count(self) -> int:
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM trials").fetchone()[0]

    def window_trials(self, kind: str, start: str, end: str) -> int:
        """How many trials of one kind have read a period. Each read weakens its evidence."""
        with self._connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM trials "
                "WHERE kind = ? AND window_start = ? AND window_end = ?",
                (kind, start, end),
            ).fetchone()[0]

    def holdout_uses(self, start: date, end: date) -> int:
        with self._connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM holdouts WHERE window_start = ? AND window_end = ?",
                (start.isoformat(), end.isoformat()),
            ).fetchone()[0]

    def record_holdout(self, now: datetime, start: date, end: date, report_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO holdouts VALUES (?, ?, ?, ?)",
                (start.isoformat(), end.isoformat(), now.isoformat(), report_id),
            )

    def save_report(self, report: dict) -> Path:
        path = self.reports / f"{report['id']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(clean(report), allow_nan=False))
        tmp.replace(path)
        return path

    def read_report(self, report_id: str) -> dict | None:
        if not REPORT_ID.match(report_id):
            return None
        path = self.reports / f"{report_id}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def list_reports(self) -> list[dict]:
        rows = []
        for path in sorted(self.reports.glob("*.json"), reverse=True):
            report = json.loads(path.read_text())
            rows.append(
                {
                    key: report.get(key)
                    for key in ("id", "kind", "created_at", "window", "config_hash", "verdict")
                }
                | {"overall": report.get("summary", {}).get("overall", {})}
            )
        return rows

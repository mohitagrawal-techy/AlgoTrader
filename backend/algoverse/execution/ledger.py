"""SQLite storage for paper positions, equity and activity."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS positions_status ON positions(status);
CREATE TABLE IF NOT EXISTS equity (
    ts TEXT PRIMARY KEY,
    realized REAL NOT NULL,
    unrealized REAL NOT NULL,
    equity REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    position_id INTEGER,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

PENDING = "pending"
OPEN = "open"
EXITING = "exiting"
CLOSED = "closed"
CANCELLED = "cancelled"
LIVE_STATUSES = (PENDING, OPEN, EXITING)


@dataclass
class PaperLeg:
    instrument_key: str
    expiry: str
    strike: float
    option_type: str
    side: int
    units: int
    entry_price: float | None = None
    mark: float | None = None
    exit_price: float | None = None
    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0


@dataclass
class Position:
    symbol: str
    strategy: str
    profile: str
    lots: int
    created_at: str
    target_pnl: float
    stop_pnl: float
    exit_dte: int
    planned_loss: float
    margin: float
    score: float
    expected_entry_debit: float
    last_spot: float
    legs: list[PaperLeg] = field(default_factory=list)
    id: int | None = None
    status: str = PENDING
    opened_at: str | None = None
    closed_at: str | None = None
    entry_costs: float = 0.0
    exit_costs: float = 0.0
    exit_reason: str | None = None
    realized_pnl: float | None = None
    unrealized_pnl: float = 0.0
    stale: bool = False
    settlement_estimated: bool = False

    @property
    def front_expiry(self) -> date:
        return min(date.fromisoformat(leg.expiry) for leg in self.legs)


def _decode(payload: str) -> Position:
    raw = json.loads(payload)
    raw["legs"] = [PaperLeg(**leg) for leg in raw["legs"]]
    return Position(**raw)


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
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

    def save(self, position: Position) -> int:
        with self._connect() as connection:
            if position.id is None:
                cursor = connection.execute(
                    "INSERT INTO positions (status, payload) VALUES (?, '')", (position.status,)
                )
                position.id = cursor.lastrowid
            connection.execute(
                "UPDATE positions SET status = ?, payload = ? WHERE id = ?",
                (position.status, json.dumps(asdict(position)), position.id),
            )
        return position.id

    def positions(self, statuses: Iterable[str]) -> list[Position]:
        wanted = tuple(statuses)
        marks = ",".join("?" * len(wanted))
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT payload FROM positions WHERE status IN ({marks}) ORDER BY id", wanted
            ).fetchall()
        return [_decode(row[0]) for row in rows]

    def realized_total(self) -> float:
        return sum(p.realized_pnl or 0.0 for p in self.positions((CLOSED,)))

    def record_equity(self, ts: datetime, realized: float, unrealized: float) -> float:
        equity = realized + unrealized
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO equity VALUES (?, ?, ?, ?)",
                (ts.isoformat(), realized, unrealized, equity),
            )
        return equity

    def baseline_equity(self, before: date) -> float | None:
        """Return the last recorded P&L level before a given date."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT equity FROM equity WHERE ts < ? ORDER BY ts DESC LIMIT 1",
                (before.isoformat(),),
            ).fetchone()
        return None if row is None else float(row[0])

    def equity_series(self, limit: int = 1000) -> list[tuple[str, float]]:
        """Return the most recent P&L points, oldest first."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT ts, equity FROM equity ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return rows[::-1]

    def log(self, ts: datetime, position_id: int | None, kind: str, detail: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO activity (ts, position_id, kind, detail) VALUES (?, ?, ?, ?)",
                (ts.isoformat(), position_id, kind, detail),
            )

    def activity(self, limit: int = 50) -> list[tuple]:
        with self._connect() as connection:
            return connection.execute(
                "SELECT ts, position_id, kind, detail FROM activity ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def get_meta(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else row[0]

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

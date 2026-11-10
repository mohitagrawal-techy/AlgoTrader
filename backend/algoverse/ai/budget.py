"""Records every AI call, enforces limits and serves cached results."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from algoverse.ai.client import LLMClient, LLMError
from algoverse.ai.schemas import OK, OVER_LIMIT, UNAVAILABLE, AiConfig

log = logging.getLogger(__name__)
Output = TypeVar("Output", bound=BaseModel)

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    prompt TEXT NOT NULL,
    output TEXT NOT NULL,
    payload TEXT,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS calls_lookup ON calls(kind, key, ts);
CREATE INDEX IF NOT EXISTS calls_day ON calls(day);
"""


class AiLog:
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

    def record(self, **fields) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO calls (ts, day, kind, key, model, status, prompt, output, payload,
                                      input_tokens, output_tokens, latency_ms)
                   VALUES (:ts, :day, :kind, :key, :model, :status, :prompt, :output, :payload,
                           :input_tokens, :output_tokens, :latency_ms)""",
                fields,
            )

    def calls_on(self, day: str) -> int:
        with self._connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM calls WHERE day = ?", (day,)
            ).fetchone()[0]

    def latest_ok(self, kind: str, key: str, model: str, since: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT payload FROM calls
                   WHERE kind = ? AND key = ? AND model = ? AND status = 'ok' AND ts >= ?
                     AND payload IS NOT NULL
                   ORDER BY id DESC LIMIT 1""",
                (kind, key, model, since),
            ).fetchone()
        return None if row is None else json.loads(row[0])


class AiGateway:
    """Runs a schema-bound model call. Failures become a status and never raise."""

    def __init__(
        self,
        client: LLMClient,
        ai_log: AiLog,
        config: AiConfig,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.client = client
        self.log = ai_log
        self.config = config
        self._clock = clock
        self._deadline = float("inf")

    @property
    def model(self) -> str:
        return self.client.model

    def begin_cycle(self) -> None:
        self._deadline = self._clock() + self.config.cycle_time_limit_seconds

    def cached(self, kind: str, key: str, now: datetime, ttl_minutes: int) -> dict | None:
        since = (now - timedelta(minutes=ttl_minutes)).isoformat()
        return self.log.latest_ok(kind, key, self.model, since)

    def call(
        self,
        kind: str,
        key: str,
        system: str,
        user: str,
        output_model: type[Output],
        now: datetime,
    ) -> tuple[Output | None, str]:
        day = now.date().isoformat()
        if self._clock() >= self._deadline or (
            self.log.calls_on(day) >= self.config.max_calls_per_day
        ):
            return None, OVER_LIMIT
        started = self._clock()
        text, input_tokens, output_tokens, parsed, status = "", 0, 0, None, UNAVAILABLE
        try:
            result = self.client.complete(system, user, output_model.model_json_schema())
            text, input_tokens, output_tokens = (
                result.text,
                result.input_tokens,
                result.output_tokens,
            )
            parsed = output_model.model_validate_json(result.text)
            status = OK
        except LLMError as exc:
            log.warning("AI call failed: %s", exc)
            text = str(exc)
        except ValidationError:
            log.warning("AI returned output that does not match the %s schema", kind)
        self.log.record(
            ts=now.isoformat(),
            day=day,
            kind=kind,
            key=key,
            model=self.model,
            status=status,
            prompt=f"{system}\n\n{user}",
            output=text,
            payload=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=int((self._clock() - started) * 1000),
        )
        return parsed, status

    def store_payload(self, kind: str, key: str, payload: dict) -> None:
        """Attach the cleaned result to the latest successful call for caching."""
        with self.log._connect() as connection:
            connection.execute(
                """UPDATE calls SET payload = ? WHERE id = (
                       SELECT MAX(id) FROM calls WHERE kind = ? AND key = ? AND status = 'ok'
                   )""",
                (json.dumps(payload), kind, key),
            )

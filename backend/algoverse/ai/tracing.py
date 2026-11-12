"""Optional LangSmith tracing of every call to the local AI model.

Off unless `.env` sets LANGSMITH_TRACING=true and LANGSMITH_API_KEY. When on, each call the
AI log records (strategy design, tuning, news research and critique, the assistant) is also
sent to LangSmith with its prompt, reply, token counts, timing and outcome.

LangSmith is a hosted service: prompts and replies leave this machine when tracing is on.
Sending happens on a background thread, so tracing can never slow down or break a call; a
failed upload is logged and dropped.
"""

from __future__ import annotations

import atexit
import json
import logging
import queue
import threading
import uuid
from datetime import UTC, datetime, timedelta

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from algoverse.settings import REPO_ROOT

log = logging.getLogger(__name__)
QUEUE_SIZE = 1000
FLUSH_SECONDS = 5.0
MAX_TEXT = 200_000


class LangSmithSettings(BaseSettings):
    """LangSmith's own variable names, read from the environment or `.env`."""

    model_config = SettingsConfigDict(
        env_prefix="LANGSMITH_", env_file=REPO_ROOT / ".env", extra="ignore"
    )

    tracing: bool = False
    api_key: SecretStr = SecretStr("")
    project: str = "algoverse"
    # The hosted service by default; set for the EU region or a self-hosted LangSmith.
    endpoint: str = "https://api.smith.langchain.com"

    @property
    def enabled(self) -> bool:
        return self.tracing and bool(self.api_key.get_secret_value())


def _messages(prompt: str) -> dict:
    """The assistant logs a JSON list of chat messages; other features log system, blank line,
    user message."""
    try:
        parsed = json.loads(prompt)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, list) and all(isinstance(m, dict) and "role" in m for m in parsed):
        return {"messages": parsed}
    return {"prompt": (prompt or "")[:MAX_TEXT]}


def build_run(fields: dict, project: str, ended: datetime | None = None) -> dict:
    """A LangSmith run from one AI log record."""
    ended = ended or datetime.now(UTC)
    latency = int(fields.get("latency_ms") or 0)
    output: dict = {"output": (fields.get("output") or "")[:MAX_TEXT]}
    payload = fields.get("payload")
    if payload:
        try:
            output["parsed"] = json.loads(payload)
        except (TypeError, ValueError):
            pass
    input_tokens = int(fields.get("input_tokens") or 0)
    output_tokens = int(fields.get("output_tokens") or 0)
    output["usage_metadata"] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    status = fields.get("status") or "unknown"
    kind = fields.get("kind") or "ai_call"
    return {
        "id": uuid.uuid4(),
        "name": kind,
        "run_type": "llm",
        "project_name": project,
        "inputs": _messages(fields.get("prompt") or ""),
        "outputs": output,
        "start_time": ended - timedelta(milliseconds=latency),
        "end_time": ended,
        "error": None if status == "ok" else f"status: {status}",
        "tags": [kind, f"status:{status}"],
        "extra": {
            "metadata": {
                "ls_provider": "ollama",
                "ls_model_name": fields.get("model"),
                "ls_model_type": "chat",
                "feature": kind,
                "key": fields.get("key"),
                "app_time": fields.get("ts"),
                "status": status,
                "latency_ms": latency,
            }
        },
    }


class Tracer:
    """Queues runs and uploads them from one background thread."""

    def __init__(self, settings: LangSmithSettings, client=None):
        self.settings = settings
        self.enabled = settings.enabled
        self._client = client
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._work, name="langsmith", daemon=True)
                self._thread.start()
                atexit.register(self.flush)

    def _get_client(self):
        if self._client is None:
            from langsmith import Client  # Imported only when tracing is on.

            self._client = Client(
                api_key=self.settings.api_key.get_secret_value(),
                api_url=self.settings.endpoint,
            )
        return self._client

    def submit(self, fields: dict) -> None:
        if not self.enabled:
            return
        try:
            self._queue.put_nowait(build_run(fields, self.settings.project))
        except queue.Full:
            log.warning("LangSmith queue is full; dropping a trace")
            return
        self._ensure_thread()

    def _work(self) -> None:
        while True:
            run = self._queue.get()
            try:
                self._get_client().create_run(**run)
            except Exception as exc:  # Tracing must never affect the app.
                log.warning("Could not send a trace to LangSmith: %s", type(exc).__name__)
            finally:
                self._queue.task_done()

    def flush(self, timeout: float = FLUSH_SECONDS) -> None:
        """Wait briefly for queued runs, then for the SDK's own batch, on shutdown."""
        if not self.enabled or self._thread is None:
            return
        done = threading.Event()

        def wait() -> None:
            self._queue.join()
            done.set()

        threading.Thread(target=wait, daemon=True).start()
        done.wait(timeout)
        client = self._client
        if client is not None and hasattr(client, "flush"):
            try:
                client.flush()
            except Exception:
                log.warning("Could not flush LangSmith traces")


_TRACER: Tracer | None = None


def get_tracer() -> Tracer:
    global _TRACER
    if _TRACER is None:
        _TRACER = Tracer(LangSmithSettings())
        if _TRACER.enabled:
            log.info("LangSmith tracing is on, project %s", _TRACER.settings.project)
    return _TRACER

"""Strategy lab: stored strategies, their test results, and background test and design jobs."""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import ValidationError

from algoverse.ai.budget import AiLog
from algoverse.ai.client import LLMClient, LLMError, OllamaClient
from algoverse.ai.schemas import load_ai_config
from algoverse.backtest.config import load_backtest_config
from algoverse.backtest.dataview import History
from algoverse.backtest.registry import Registry
from algoverse.dashboard import clean
from algoverse.lab.designer import design_strategies, feedback_row
from algoverse.lab.evaluate import (
    evaluate_specs,
    rank_key,
    sessions_needed,
    split_windows,
)
from algoverse.lab.ladder import ROOTS, ladder_windows, run_ladder
from algoverse.lab.seeds import TEMPLATES
from algoverse.lab.spec import (
    SIGNALS,
    StrategySpec,
    complexity_level,
    describe_entry,
    describe_legs,
    has_undefined_risk,
    make_id,
    signature,
)
from algoverse.lab.technical import catalog as technical_catalog
from algoverse.risk.config import load_risk_config

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
DESIGN_TIMEOUT_SECONDS = 420
MAX_STORED = 200


def _without_detail(result: dict | None) -> dict | None:
    """The list view leaves out per-trade detail, which only the backtest page needs."""
    if result is None:
        return None
    return {key: value for key, value in result.items() if key != "detail"} | {
        "has_detail": bool(result.get("detail"))
    }


class LabError(Exception):
    """The request cannot be carried out. The message is safe to show the user."""


class LabBusy(LabError):
    """Another lab job is still running."""


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean(payload), allow_nan=False))
    os.replace(tmp, path)


class LabService:
    def __init__(
        self,
        data_dir: Path,
        config_dir: Path,
        ai_enabled: bool = True,
        client: LLMClient | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.config_dir = Path(config_dir)
        self.ai_enabled = ai_enabled
        self._client = client
        self._folder = self.data_dir / "lab"
        self._lock = threading.Lock()
        self._job: dict | None = None

    # ---- Storage -------------------------------------------------------------------------

    def _spec_path(self, spec_id: str) -> Path:
        return self._folder / "strategies" / f"{spec_id}.json"

    def _result_path(self, spec_id: str) -> Path:
        return self._folder / "results" / f"{spec_id}.json"

    def specs(self) -> list[StrategySpec]:
        stored = []
        for path in sorted((self._folder / "strategies").glob("*.json")):
            try:
                stored.append(StrategySpec.model_validate_json(path.read_text()))
            except ValidationError:
                log.warning("Skipping unreadable strategy file %s", path.name)
        return [*TEMPLATES, *stored]

    def spec(self, spec_id: str) -> StrategySpec | None:
        return next((spec for spec in self.specs() if spec.id == spec_id), None)

    def result(self, spec: StrategySpec) -> dict | None:
        """The latest result, unless the rules changed since it was produced."""
        try:
            result = json.loads(self._result_path(spec.id).read_text())
        except FileNotFoundError:
            return None
        return result if result.get("signature") == signature(spec) else None

    def save(self, payload: dict, now: datetime) -> StrategySpec:
        """Create or replace a user strategy. Templates cannot be overwritten."""
        try:
            draft = StrategySpec.model_validate(
                {**payload, "id": "draft", "source": payload.get("source", "user")}
            )
        except ValidationError as exc:
            problem = exc.errors()[0]
            where = ".".join(str(part) for part in problem["loc"])
            raise LabError(f"{where}: {problem['msg']}" if where else problem["msg"]) from exc
        existing = payload.get("id")
        stored_ids = {spec.id for spec in self.specs() if spec.source != "template"}
        if existing in stored_ids:
            spec_id = existing
        else:
            if len(stored_ids) >= MAX_STORED:
                raise LabError(f"The lab holds at most {MAX_STORED} saved strategies.")
            spec_id = make_id(draft.name, signature(draft))
        spec = draft.model_copy(
            update={"id": spec_id, "source": "user", "created_at": now.isoformat()}
        )
        _write(self._spec_path(spec.id), spec.model_dump())
        return spec

    def delete(self, spec_id: str) -> None:
        spec = self.spec(spec_id)
        if spec is None or spec.source == "template":
            raise LabError("Only saved strategies can be deleted.")
        self._spec_path(spec.id).unlink(missing_ok=True)
        self._result_path(spec.id).unlink(missing_ok=True)

    # ---- Views ---------------------------------------------------------------------------

    def overview(self) -> dict:
        config = load_backtest_config(self.config_dir)
        sessions = History(self.data_dir).sessions
        windows = split_windows(sessions, config)
        rows = []
        for spec in self.specs():
            rows.append(
                {
                    "spec": spec.model_dump(),
                    "legs_text": describe_legs(spec),
                    "undefined_risk": has_undefined_risk(spec),
                    "level": complexity_level(spec),
                    "entry_text": describe_entry(spec),
                    "result": _without_detail(self.result(spec)),
                }
            )
        rows.sort(key=lambda row: rank_key(row["result"]))
        return clean(
            {
                "history": {
                    "sessions": len(sessions),
                    "needed": sessions_needed(config),
                    "ready": windows is not None,
                    "start": sessions[0] if sessions else None,
                    "end": sessions[-1] if sessions else None,
                },
                "windows": windows.__dict__ if windows else None,
                "min_test_trades": config.lab.min_test_trades,
                "tested": sum(1 for row in rows if row["result"]),
                "trials_logged": Registry(self.data_dir).trial_count(),
                "ai": {"enabled": self.ai_enabled, "model": load_ai_config(self.config_dir).model},
                "defaults": {"rounds": config.lab.rounds, "per_round": config.lab.per_round},
                "job": self._job,
                "symbols": config.symbols,
                "signals": [{"name": k, "label": v} for k, v in SIGNALS.items()],
                "technical": technical_catalog(),
                "ladder": self._latest_ladder(),
                "ladder_ready": ladder_windows(sessions, config) is not None,
                "strategies": rows,
            }
        )

    def backtest_detail(self, spec_id: str) -> dict:
        """One strategy's latest result with every trade and day, and the daily bars of each
        underlying it traded, for its backtest page."""
        spec = self.spec(spec_id)
        if spec is None:
            raise LabError("That strategy no longer exists.")
        result = self.result(spec)
        history = History(self.data_dir)
        bars = {}
        if result and result.get("detail"):
            traded = {
                trade["symbol"]
                for part in result["detail"].values()
                for trade in part.get("trades", [])
            }
            windows = result.get("windows") or {}
            start = str(windows.get("train_start") or "")
            for symbol in sorted(traded):
                frame = history.bars(symbol)
                days = pd.to_datetime(frame["ts"]).dt.date.astype(str)
                frame = frame.assign(day=days)[days >= start]
                bars[symbol] = [
                    {
                        "day": row.day,
                        "open": float(row.open),
                        "high": float(row.high),
                        "low": float(row.low),
                        "close": float(row.close),
                    }
                    for row in frame.itertuples()
                ]
        return clean(
            {
                "spec": spec.model_dump(),
                "legs_text": describe_legs(spec),
                "entry_text": describe_entry(spec),
                "undefined_risk": has_undefined_risk(spec),
                "level": complexity_level(spec),
                "capital": load_risk_config(self.config_dir).capital,
                "result": result,
                "bars": bars,
            }
        )

    # ---- Work ----------------------------------------------------------------------------

    def _ladder_path(self) -> Path:
        return self._folder / "ladder.json"

    def _latest_ladder(self) -> dict | None:
        try:
            return json.loads(self._ladder_path().read_text())
        except FileNotFoundError:
            return None

    def _ladder_roots(self, start_id: str | None) -> list[StrategySpec]:
        if start_id is None:
            return ROOTS
        spec = self.spec(start_id)
        if spec is None:
            raise LabError("That strategy no longer exists.")
        return [spec.model_copy(update={"rung": 1, "parent_id": None, "step": None})]

    def _tested_count(self) -> int:
        return sum(1 for spec in self.specs() if self.result(spec) is not None)

    def run_ladder(
        self,
        start_id: str | None,
        now: datetime,
        note: Callable[[str], None] = lambda _: None,
    ) -> dict:
        """Climb from simple to complex, then spend the final test on one winner only."""
        config = load_backtest_config(self.config_dir)
        if ladder_windows(History(self.data_dir).sessions, config) is None:
            raise LabError("The ladder needs more history than is stored.")
        roots = self._ladder_roots(start_id)
        outcome = run_ladder(self.data_dir, self.config_dir, roots, now, note)
        report, winner = outcome["report"], outcome["winner"]
        if winner is not None:
            note(f"Final test on the one winner: {winner.name}")
            stored = winner.model_copy(update={"created_at": now.isoformat()})
            _write(self._spec_path(stored.id), stored.model_dump())
            trials = self._tested_count() + outcome["trials"]
            result = self.run_backtest([stored.id], now, note, trials)[0]
            report["final"] = {
                key: result[key] for key in ("verdict", "test", "test_2x", "evidence")
            }
        registry = Registry(self.data_dir)
        windows = report["windows"]
        report["final_period_uses"] = registry.window_trials(
            "lab_test", windows["final_start"], windows["final_end"]
        )
        _write(self._ladder_path(), report)
        return report

    def _require_history(self) -> None:
        config = load_backtest_config(self.config_dir)
        sessions = History(self.data_dir).sessions
        if split_windows(sessions, config) is None:
            raise LabError(
                f"Testing needs {sessions_needed(config)} sessions of history and "
                f"{len(sessions)} are stored. Run `algoverse history-import` or see "
                "`algoverse --help` for other import commands."
            )

    def run_backtest(
        self,
        ids: list[str] | None,
        now: datetime,
        note: Callable[[str], None] = lambda _: None,
        trials: int | None = None,
    ) -> list[dict]:
        self._require_history()
        wanted = [s for s in self.specs() if ids is None or s.id in ids]
        if not wanted:
            raise LabError("No matching strategies to test.")
        note(f"Testing {len(wanted)} strategies")
        results = evaluate_specs(
            self.data_dir,
            self.config_dir,
            wanted,
            now,
            lambda done, total, name: note(f"Tested {done} of {total}: {name}"),
            trials if trials is not None else max(self._tested_count(), len(wanted)),
        )
        for result in results:
            _write(self._result_path(result["strategy_id"]), result)
        return results

    def _design_client(self) -> LLMClient:
        if self._client is not None:
            return self._client
        config = load_ai_config(self.config_dir)
        if not (self.ai_enabled and config.enabled):
            raise LabError("The AI model is turned off in config/ai.yaml.")
        return OllamaClient(
            config.model_copy(update={"request_timeout_seconds": DESIGN_TIMEOUT_SECONDS})
        )

    def run_generate(
        self,
        now: datetime,
        rounds: int | None = None,
        per_round: int | None = None,
        guidance: str = "",
        note: Callable[[str], None] = lambda _: None,
    ) -> dict:
        """Design, test on training data, show the AI those results, and repeat."""
        self._require_history()
        client = self._design_client()
        config = load_backtest_config(self.config_dir)
        risk = load_risk_config(self.config_dir)
        rounds = rounds or config.lab.rounds
        per_round = per_round or config.lab.per_round
        account = {
            "capital_inr": risk.capital,
            "max_loss_per_trade_inr": risk.amount(risk.max_loss_per_trade_pct),
            "underlyings": config.symbols,
        }
        untested = [s for s in TEMPLATES if self.result(s) is None]
        if untested:
            note("Testing the baseline templates first")
            self.run_backtest([s.id for s in untested], now, note)
        ai_log = AiLog(self.data_dir / "ai.sqlite")
        created: list[str] = []
        dropped: list[str] = []
        for number in range(1, rounds + 1):
            specs = self.specs()
            tested = [feedback_row(spec, self.result(spec)) for spec in specs]
            note(f"Round {number} of {rounds}: the AI is designing {per_round} strategies")
            try:
                new, notes = design_strategies(
                    client,
                    ai_log,
                    now,
                    per_round,
                    number,
                    rounds,
                    account,
                    tested,
                    {signature(spec) for spec in specs},
                    guidance,
                )
            except LLMError as exc:
                raise LabError(f"The AI model failed in round {number}: {exc}") from exc
            dropped += notes
            if not new:
                note(f"Round {number}: no new valid strategies were proposed")
                continue
            for spec in new:
                _write(self._spec_path(spec.id), spec.model_dump())
            self.run_backtest([spec.id for spec in new], now, note)
            created += [spec.id for spec in new]
        return {"created": created, "dropped": dropped}

    # ---- Background jobs -----------------------------------------------------------------

    def _start(self, kind: str, work: Callable[[Callable[[str], None]], str]) -> dict:
        with self._lock:
            if self._job and self._job["state"] == "running":
                raise LabBusy("A lab job is already running.")
            job = {
                "kind": kind,
                "state": "running",
                "message": "Starting",
                "started_at": datetime.now(IST).isoformat(),
                "finished_at": None,
            }
            self._job = job

        def note(message: str) -> None:
            job["message"] = message

        def run() -> None:
            try:
                job["message"] = work(note)
                job["state"] = "done"
            except LabError as exc:
                job["state"], job["message"] = "failed", str(exc)
            except Exception:
                log.exception("Lab job failed")
                job["state"], job["message"] = "failed", "The job failed. See the server log."
            job["finished_at"] = datetime.now(IST).isoformat()

        threading.Thread(target=run, daemon=True).start()
        return job

    def start_backtest(self, ids: list[str] | None, now: datetime) -> dict:
        self._require_history()

        def work(note) -> str:
            return f"Tested {len(self.run_backtest(ids, now, note))} strategies."

        return self._start("backtest", work)

    def start_ladder(self, start_id: str | None, now: datetime) -> dict:
        config = load_backtest_config(self.config_dir)
        if ladder_windows(History(self.data_dir).sessions, config) is None:
            raise LabError(
                f"The ladder needs more than {sessions_needed(config)} sessions of history, "
                "so each stage has enough to judge."
            )
        self._ladder_roots(start_id)

        def work(note) -> str:
            report = self.run_ladder(start_id, now, note)
            if report["winner_id"] is None:
                return "No simple starting strategy had enough trades to build on."
            return f"Finished after {report['trials']} tested strategies."

        return self._start("ladder", work)

    def start_generate(
        self, now: datetime, rounds: int | None, per_round: int | None, guidance: str
    ) -> dict:
        self._require_history()
        self._design_client()

        def work(note) -> str:
            outcome = self.run_generate(now, rounds, per_round, guidance, note)
            extra = (
                f" {len(outcome['dropped'])} proposals were dropped." if outcome["dropped"] else ""
            )
            return f"The AI created and tested {len(outcome['created'])} strategies.{extra}"

        return self._start("generate", work)

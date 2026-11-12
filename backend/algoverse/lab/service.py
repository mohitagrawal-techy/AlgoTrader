"""Strategy lab: stored strategies, their test results, and background test and design jobs."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
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
from algoverse.backtest.progress import set_progress
from algoverse.backtest.registry import Registry
from algoverse.dashboard import clean
from algoverse.data.candles import MINUTE
from algoverse.data.store import SnapshotStore
from algoverse.lab.chart import ChartRequest, chart
from algoverse.lab.designer import _rules_help, design_strategies, feedback_row
from algoverse.lab.evaluate import (
    HELD_UP,
    UNPROVEN,
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
from algoverse.lab.tuner import (
    Scorer,
    Suggestions,
    ai_changes,
    built_in_changes,
    grow,
    new_node,
    public,
)
from algoverse.risk.config import load_risk_config

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
DESIGN_TIMEOUT_SECONDS = 420
MAX_STORED = 200
MAX_TUNING_SESSIONS = 30
# Autopilot limits. Each cycle designs, tests and tunes, which takes minutes, not seconds.
AUTOPILOT_FAILURES = 3
# Verdicts worth keeping in the lab; the rest are archived when autopilot made them.
KEEP_VERDICTS = (HELD_UP, UNPROVEN)


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
        self._stop = threading.Event()
        self._bars: tuple[tuple, dict[str, pd.DataFrame]] | None = None

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
        _write(self._spec_path(spec.id), spec.model_dump(mode="json"))
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
                    "spec": spec.model_dump(mode="json"),
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
                "autopilot": self.autopilot(),
                "archived": len(list((self._archive_folder() / "strategies").glob("*.json"))),
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
                "spec": spec.model_dump(mode="json"),
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
        progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        """Climb from simple to complex, then spend the final test on one winner only."""
        config = load_backtest_config(self.config_dir)
        if ladder_windows(History(self.data_dir).sessions, config) is None:
            raise LabError("The ladder needs more history than is stored.")
        roots = self._ladder_roots(start_id)
        outcome = run_ladder(self.data_dir, self.config_dir, roots, now, note, progress)
        report, winner = outcome["report"], outcome["winner"]
        if winner is not None:
            note(f"Final test on the one winner: {winner.name}")
            stored = winner.model_copy(update={"created_at": now.isoformat()})
            _write(self._spec_path(stored.id), stored.model_dump(mode="json"))
            trials = self._tested_count() + outcome["trials"]
            result = self.run_backtest([stored.id], now, note, trials, progress)[0]
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
        progress: Callable[[int, int], None] | None = None,
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
            progress,
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
        progress: Callable[[int, int], None] | None = None,
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
            self.run_backtest([s.id for s in untested], now, note, progress=progress)
        ai_log = AiLog(self.data_dir / "ai.sqlite")
        created: list[str] = []
        dropped: list[str] = []
        for number in range(1, rounds + 1):
            specs = self.specs()
            archived = self._archived()
            # Archived strategies still count: the AI learns from them and must not repeat them.
            tested = [feedback_row(spec, result) for spec, result in archived] + [
                feedback_row(spec, self.result(spec)) for spec in specs
            ]
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
                    {signature(spec) for spec in specs} | {signature(spec) for spec, _ in archived},
                    guidance,
                )
            except LLMError as exc:
                raise LabError(f"The AI model failed in round {number}: {exc}") from exc
            dropped += notes
            if not new:
                note(f"Round {number}: no new valid strategies were proposed")
                continue
            for spec in new:
                _write(self._spec_path(spec.id), spec.model_dump(mode="json"))
            self.run_backtest([spec.id for spec in new], now, note, progress=progress)
            created += [spec.id for spec in new]
        return {"created": created, "dropped": dropped}

    # ---- Tuning --------------------------------------------------------------------------

    def _tuning_path(self, session_id: str) -> Path:
        if not session_id.replace("-", "").replace("_", "").isalnum():
            raise LabError("That tuning session does not exist.")
        return self._folder / "tuning" / f"{session_id}.json"

    def _tuning(self, session_id: str) -> dict:
        try:
            return json.loads(self._tuning_path(session_id).read_text())
        except FileNotFoundError as exc:
            raise LabError("That tuning session does not exist.") from exc

    def tuning_overview(self) -> dict:
        config = load_backtest_config(self.config_dir)
        sessions = []
        for path in sorted((self._folder / "tuning").glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text())
            except ValueError:
                continue
            best = data["nodes"].get(data.get("best_id") or data["root_id"], {})
            root = data["nodes"].get(data["root_id"], {})
            sessions.append(
                {
                    "id": data["id"],
                    "root_name": data["root_name"],
                    "created_at": data["created_at"],
                    "updated_at": data.get("updated_at"),
                    "tested": len(data["nodes"]) - 1,
                    "kept": sum(1 for n in data["nodes"].values() if n.get("accepted")),
                    "root_validate": root.get("validate"),
                    "best_validate": best.get("validate"),
                    "best_label": best.get("label"),
                    "final": data.get("final"),
                }
            )
        return clean(
            {
                "ready": ladder_windows(History(self.data_dir).sessions, config) is not None,
                "ai": {"enabled": self.ai_enabled, "model": load_ai_config(self.config_dir).model},
                "job": self._job,
                "sessions": sessions[:MAX_TUNING_SESSIONS],
                "strategies": [
                    {"id": spec.id, "name": spec.name, "source": spec.source}
                    for spec in self.specs()
                ],
                "technical": technical_catalog(),
                "signals": [{"name": k, "label": v} for k, v in SIGNALS.items()],
            }
        )

    def tuning_session(self, session_id: str) -> dict:
        return public(self._tuning(session_id))

    def _suggester(self, now: datetime, use_ai: bool, guidance: str, note):
        risk = load_risk_config(self.config_dir)
        account = {
            "capital_inr": risk.capital,
            "max_loss_per_trade_inr": risk.amount(risk.max_loss_per_trade_pct),
        }
        client = self._design_client() if use_ai else None
        ai_log = AiLog(self.data_dir / "ai.sqlite")
        help_text = _rules_help()

        def suggest(spec, feedback, tried, count) -> Suggestions:
            if client is not None:
                try:
                    return ai_changes(
                        client,
                        ai_log,
                        now,
                        spec,
                        feedback,
                        tried,
                        count,
                        account,
                        guidance,
                        help_text,
                    )
                except LLMError as exc:
                    note(f"The AI model failed ({exc}); using built-in changes this round")
                    fallback = Suggestions(
                        "The AI model was unavailable, so these are built-in nudges to the "
                        "strategy's own numbers.",
                        built_in_changes(spec),
                        "built_in",
                        [f"AI model failed: {exc}"],
                    )
                    return fallback
            return Suggestions(
                "Built-in nudges to the strategy's own numbers: exits, strikes, timing and "
                "rule settings, one at a time.",
                built_in_changes(spec),
                "built_in",
            )

        return suggest

    def _tuning_windows(self):
        config = load_backtest_config(self.config_dir)
        windows = ladder_windows(History(self.data_dir).sessions, config)
        if windows is None:
            raise LabError(
                f"Tuning needs more than {sessions_needed(config)} sessions of history, so "
                "design and unseen sessions each have enough to judge."
            )
        return config, windows

    def run_tuning(
        self,
        start_id: str | None,
        now: datetime,
        session_id: str | None = None,
        node_id: str | None = None,
        rounds: int | None = None,
        per_round: int | None = None,
        guidance: str = "",
        use_ai: bool = True,
        note: Callable[[str], None] = lambda _: None,
        new_id: str | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        """Start a tuning tree from a strategy, or grow an existing one from one of its nodes."""
        config, windows = self._tuning_windows()
        rounds = rounds or config.lab.ladder_rungs
        per_round = per_round or min(config.lab.ladder_steps, 6)
        scorer = Scorer(self.data_dir, self.config_dir, windows, now, progress)
        if session_id is None:
            spec = self.spec(start_id or "")
            if spec is None:
                raise LabError("That strategy no longer exists.")
            note(f"Testing the starting version: {spec.name}")
            scored = scorer.score([spec])[spec.id]
            session = {
                "id": new_id or self._new_tuning_id(spec.id, now),
                "root_id": spec.id,
                "root_name": spec.name.split(", tuned")[0][:40],
                "created_at": now.isoformat(),
                "windows": windows.__dict__,
                "nodes": {spec.id: new_node(spec, scored, 0, None)},
                "rounds": [],
                "best_id": spec.id,
                "trials": 0,
                "guidance": guidance[:500],
            }
            node_id = spec.id
        else:
            session = self._tuning(session_id)
            if node_id not in session["nodes"]:
                raise LabError("That version is not in this tuning session.")
            session.pop("final", None)
        suggest = self._suggester(now, use_ai, guidance, note)
        grow(session, scorer, node_id, rounds, per_round, suggest, note)
        best = session["nodes"][session["best_id"]]
        if best["depth"] > 0:
            note(f"Final test on the one chosen version: {best['label']}")
            stored = StrategySpec.model_validate(best["spec"]).model_copy(
                update={"created_at": now.isoformat()}
            )
            _write(self._spec_path(stored.id), stored.model_dump(mode="json"))
            trials = self._tested_count() + session["trials"]
            result = self.run_backtest([stored.id], now, note, trials, progress)[0]
            session["final"] = {
                "id": stored.id,
                **{key: result[key] for key in ("verdict", "test", "test_2x", "evidence")},
            }
        root = self.spec(session["root_id"])
        root_result = self.result(root) if root else None
        session["root_final"] = (
            {key: root_result[key] for key in ("verdict", "test", "test_2x")}
            if root_result
            else None
        )
        session["final_period_uses"] = Registry(self.data_dir).window_trials(
            "lab_test", windows.final_start, windows.final_end
        )
        session["updated_at"] = now.isoformat()
        _write(self._tuning_path(session["id"]), session)
        return session

    @staticmethod
    def _new_tuning_id(spec_id: str, now: datetime) -> str:
        return f"{now:%Y%m%d-%H%M%S}-{spec_id[:24]}"

    def save_tuned(self, session_id: str, node_id: str, now: datetime) -> str:
        session = self._tuning(session_id)
        node = session["nodes"].get(node_id)
        if node is None:
            raise LabError("That version is not in this tuning session.")
        spec = StrategySpec.model_validate(node["spec"]).model_copy(
            update={"created_at": now.isoformat()}
        )
        if self.spec(spec.id) is None:
            _write(self._spec_path(spec.id), spec.model_dump(mode="json"))
        return spec.id

    def start_tuning(
        self,
        start_id: str | None,
        now: datetime,
        session_id: str | None = None,
        node_id: str | None = None,
        rounds: int | None = None,
        per_round: int | None = None,
        guidance: str = "",
        use_ai: bool = True,
    ) -> dict:
        self._tuning_windows()
        if session_id is None and self.spec(start_id or "") is None:
            raise LabError("Choose a strategy to tune.")
        if session_id is not None and node_id not in self._tuning(session_id)["nodes"]:
            raise LabError("That version is not in this tuning session.")
        if use_ai:
            self._design_client()

        target = session_id or self._new_tuning_id(start_id or "", now)

        def work(note, progress) -> str:
            session = self.run_tuning(
                start_id,
                now,
                session_id,
                node_id,
                rounds,
                per_round,
                guidance,
                use_ai,
                note,
                new_id=target,
                progress=progress,
            )
            kept = sum(1 for n in session["nodes"].values() if n.get("accepted"))
            return f"Tested {len(session['nodes']) - 1} changes, kept {kept}."

        job = self._start("tune", work)
        job["session_id"] = target
        return job

    # ---- Chart ----------------------------------------------------------------------------

    def _daily_bars(self, symbol: str) -> pd.DataFrame:
        """Daily candles as the backtests read them, rebuilt only when history changes."""
        history = History(self.data_dir)
        sessions = history.sessions
        key = (len(sessions), sessions[-1] if sessions else None)
        if self._bars is None or self._bars[0] != key:
            self._bars = (key, {})
        cache = self._bars[1]
        if symbol not in cache:
            cache[symbol] = history.bars(symbol)
        return cache[symbol]

    def chart(self, request: ChartRequest) -> dict:
        config = load_backtest_config(self.config_dir)
        if request.symbol not in config.symbols:
            raise LabError(f"Choose one of {', '.join(config.symbols)}.")
        daily = self._daily_bars(request.symbol)
        minutes = (
            SnapshotStore(self.data_dir).read_candles(request.symbol, MINUTE)
            if request.timeframe in ("5m", "15m", "1h")
            else pd.DataFrame()
        )
        return clean(chart(daily, minutes, request))

    # ---- Autopilot ------------------------------------------------------------------------

    def _autopilot_path(self) -> Path:
        return self._folder / "autopilot.json"

    def _archive_folder(self) -> Path:
        return self._folder / "archive"

    def _archived(self) -> list[tuple[StrategySpec, dict | None]]:
        """Strategies autopilot moved out of the lab, with their last results."""
        found = []
        for path in sorted((self._archive_folder() / "strategies").glob("*.json")):
            try:
                spec = StrategySpec.model_validate_json(path.read_text())
            except ValidationError:
                continue
            try:
                result = json.loads((self._archive_folder() / "results" / path.name).read_text())
            except (FileNotFoundError, ValueError):
                result = None
            found.append((spec, result))
        return found

    def _archive(self, spec_id: str) -> None:
        """Move a strategy and its result out of the lab. Nothing is deleted."""
        for kind, source in (
            ("strategies", self._spec_path(spec_id)),
            ("results", self._result_path(spec_id)),
        ):
            if source.exists():
                target = self._archive_folder() / kind / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)

    def autopilot(self) -> dict | None:
        try:
            state = json.loads(self._autopilot_path().read_text())
        except FileNotFoundError:
            return None
        job = self._job
        live = job is not None and job["kind"] == "autopilot" and job["state"] == "running"
        if state.get("running") and not live:
            state["running"] = False
            state["ended"] = "Interrupted: the server restarted or the job failed."
        return state

    @staticmethod
    def _train_summary(result: dict | None) -> dict | None:
        if result is None:
            return None
        train = result.get("train") or {}
        return {
            key: train.get(key) for key in ("trades", "net_pnl", "win_rate", "expectancy", "sharpe")
        }

    @staticmethod
    def _pick(candidates: list[tuple[StrategySpec, dict | None]], min_trades: int):
        """The design worth tuning: best training Sharpe among those with enough trades.

        Only training results are used, so picking never looks at the test period."""
        usable = [
            (spec, result)
            for spec, result in candidates
            if result and (result["train"].get("trades") or 0) >= min_trades
        ]
        if not usable:
            return None
        return max(usable, key=lambda item: item[1]["train"].get("sharpe") or -99.0)[0]

    def run_autopilot(
        self,
        now: datetime,
        cycles: int,
        hours: float,
        per_cycle: int,
        tune_rounds: int,
        guidance: str,
        stop: threading.Event,
        note: Callable[[str], None] = lambda _: None,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        """Repeat: the AI designs strategies, the best on training data is tuned one setting
        at a time, and it gets one final test. Strategies that did not hold up are archived,
        so the lab keeps only the ones worth a look. Stops on request, at the cycle or time
        limit, or after repeated AI failures."""
        self._require_history()
        config, _ = self._tuning_windows()
        deadline = time.monotonic() + hours * 3600
        state = {
            "started_at": now.isoformat(),
            "finished_at": None,
            "running": True,
            "ended": None,
            "settings": {
                "cycles": cycles,
                "hours": hours,
                "per_cycle": per_cycle,
                "tune_rounds": tune_rounds,
                "guidance": guidance[:500],
            },
            "cycles": [],
        }
        _write(self._autopilot_path(), state)
        failures = 0
        ended = f"Finished all {cycles} cycles"
        for number in range(1, cycles + 1):
            if stop.is_set():
                ended = "Stopped on request"
                break
            if time.monotonic() > deadline:
                ended = f"Reached the {hours:g} hour limit"
                break
            if len(self.specs()) - len(TEMPLATES) >= MAX_STORED:
                ended = f"The lab is full ({MAX_STORED} strategies). Delete some to continue."
                break
            cycle_now = datetime.now(IST)
            cycle: dict = {
                "number": number,
                "started_at": cycle_now.isoformat(),
                "designed": [],
                "dropped": 0,
                "picked": None,
                "session_id": None,
                "final": None,
                "kept": [],
                "archived": 0,
                "message": None,
            }
            state["cycles"].append(cycle)
            _write(self._autopilot_path(), state)

            def step(message: str, number=number) -> None:
                note(f"Cycle {number} of {cycles}: {message}")

            step(f"the AI is designing {per_cycle} strategies")
            try:
                outcome = self.run_generate(cycle_now, 1, per_cycle, guidance, step, progress)
            except LabError as exc:
                failures += 1
                cycle["message"] = str(exc)
                _write(self._autopilot_path(), state)
                if failures >= AUTOPILOT_FAILURES:
                    ended = f"The AI failed {AUTOPILOT_FAILURES} cycles in a row: {exc}"
                    break
                continue
            failures = 0
            designed = [
                (spec, self.result(spec))
                for spec in (self.spec(i) for i in outcome["created"])
                if spec is not None
            ]
            cycle["dropped"] = len(outcome["dropped"])
            cycle["designed"] = [
                {
                    "id": spec.id,
                    "name": spec.name,
                    "train": self._train_summary(result),
                    "verdict": result["verdict"] if result else None,
                }
                for spec, result in designed
            ]
            made = [spec.id for spec, _ in designed]
            picked = self._pick(designed, config.lab.min_test_trades)
            if picked is None:
                cycle["message"] = (
                    "No design had enough training trades to tune."
                    if designed
                    else "The AI proposed nothing new."
                )
            elif not stop.is_set():
                cycle["picked"] = {"id": picked.id, "name": picked.name}
                _write(self._autopilot_path(), state)
                step(f"tuning {picked.name}")
                try:
                    session = self.run_tuning(
                        picked.id,
                        datetime.now(IST),
                        rounds=tune_rounds,
                        guidance=guidance,
                        note=step,
                        progress=progress,
                    )
                except LabError as exc:
                    cycle["message"] = f"Tuning failed: {exc}"
                else:
                    cycle["session_id"] = session["id"]
                    final = session.get("final")
                    if final:
                        made.append(final["id"])
                        cycle["final"] = {
                            "id": final["id"],
                            "verdict": final["verdict"],
                            "test": final.get("test"),
                        }
                    else:
                        cycle["message"] = "No change beat the original on unseen sessions."
            for spec_id in dict.fromkeys(made):
                spec = self.spec(spec_id)
                result = self.result(spec) if spec else None
                if result and result["verdict"] in KEEP_VERDICTS:
                    cycle["kept"].append(
                        {"id": spec_id, "name": spec.name, "verdict": result["verdict"]}
                    )
                elif spec is not None and spec.source != "template":
                    self._archive(spec_id)
                    cycle["archived"] += 1
            cycle["finished_at"] = datetime.now(IST).isoformat()
            _write(self._autopilot_path(), state)
        state.update(running=False, ended=ended, finished_at=datetime.now(IST).isoformat())
        _write(self._autopilot_path(), state)
        return state

    def start_autopilot(
        self,
        now: datetime,
        cycles: int,
        hours: float,
        per_cycle: int,
        tune_rounds: int,
        guidance: str,
    ) -> dict:
        self._require_history()
        self._tuning_windows()
        self._design_client()
        stop = threading.Event()

        def work(note, progress) -> str:
            state = self.run_autopilot(
                now, cycles, hours, per_cycle, tune_rounds, guidance, stop, note, progress
            )
            kept = sum(len(c["kept"]) for c in state["cycles"])
            return f"{state['ended']}. {len(state['cycles'])} cycles, {kept} strategies kept."

        with self._lock:
            self._stop = stop
        return self._start("autopilot", work)

    def stop_autopilot(self) -> dict:
        job = self._job
        if not job or job["kind"] != "autopilot" or job["state"] != "running":
            raise LabError("Autopilot is not running.")
        self._stop.set()
        job["message"] = "Stopping after the current step"
        return job

    # ---- Background jobs -----------------------------------------------------------------

    def _start(self, kind: str, work: Callable[..., str]) -> dict:
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

        def progress(done: int, total: int) -> None:
            set_progress(job, done, total)

        def run() -> None:
            try:
                job["message"] = work(note, progress)
                job.pop("progress", None)
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

        def work(note, progress) -> str:
            return f"Tested {len(self.run_backtest(ids, now, note, progress=progress))} strategies."

        return self._start("backtest", work)

    def start_ladder(self, start_id: str | None, now: datetime) -> dict:
        config = load_backtest_config(self.config_dir)
        if ladder_windows(History(self.data_dir).sessions, config) is None:
            raise LabError(
                f"The ladder needs more than {sessions_needed(config)} sessions of history, "
                "so each stage has enough to judge."
            )
        self._ladder_roots(start_id)

        def work(note, progress) -> str:
            report = self.run_ladder(start_id, now, note, progress)
            if report["winner_id"] is None:
                return "No simple starting strategy had enough trades to build on."
            return f"Finished after {report['trials']} tested strategies."

        return self._start("ladder", work)

    def start_generate(
        self, now: datetime, rounds: int | None, per_round: int | None, guidance: str
    ) -> dict:
        self._require_history()
        self._design_client()

        def work(note, progress) -> str:
            outcome = self.run_generate(now, rounds, per_round, guidance, note, progress)
            extra = (
                f" {len(outcome['dropped'])} proposals were dropped." if outcome["dropped"] else ""
            )
            return f"The AI created and tested {len(outcome['created'])} strategies.{extra}"

        return self._start("generate", work)

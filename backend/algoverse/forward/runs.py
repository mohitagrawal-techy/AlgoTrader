"""Locked forward runs on live quotes with paper fills."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import algoverse
from algoverse.ai.event_store import load_live_calendar
from algoverse.ai.schemas import load_ai_config
from algoverse.ai.service import AiService, build_ai_service
from algoverse.backtest.config import config_hash, load_backtest_config
from algoverse.backtest.engine import daily_pnl_series, regime_label, trades_frame
from algoverse.backtest.fills import spread_percent
from algoverse.backtest.registry import Registry
from algoverse.backtest.report import daily_stats, max_drawdown, trade_stats
from algoverse.dashboard import clean, idea_id
from algoverse.data.store import SnapshotStore
from algoverse.evaluation.config import load_scoring_config
from algoverse.execution.cycle import CycleDeps, CycleResult, prepare_chains, run_cycle
from algoverse.execution.ledger import CANCELLED, CLOSED, EXITING, LIVE_STATUSES, OPEN, Ledger
from algoverse.execution.paper import PaperBroker
from algoverse.forward.drift import drift_checks, extract_baseline
from algoverse.forward.gates import GateInputs, GatesConfig, evaluate_gates, load_gates
from algoverse.forward.shadow import STAGES, as_one_lot, select
from algoverse.risk.config import load_risk_config
from algoverse.risk.costs import load_cost_model
from algoverse.settings import Settings

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
PACKAGE_ROOT = Path(algoverse.__file__).parent
DECISION_PACKAGES = ("ai", "analytics", "evaluation", "execution", "risk", "strategies")
# events.yaml is excluded on purpose: keeping the event calendar current is not a strategy change.
LOCKED_CONFIG_FILES = ("scoring.yaml", "risk.yaml", "costs.yaml", "ai.yaml", "universe.yaml")
ACTIVE, ENDED, HALTED = "active", "ended", "halted"
CHAMPION, CHALLENGER = "champion", "challenger"
EQUITY_POINTS = 1000
UNLIMITED_PCT = 1e9

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    end_reason TEXT,
    promoted_at TEXT,
    min_score REAL NOT NULL,
    disabled_profiles TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    backtest_hash TEXT NOT NULL,
    gates TEXT NOT NULL,
    report_id TEXT,
    holdout_id TEXT,
    baseline TEXT
);
CREATE TABLE IF NOT EXISTS labels (
    run_id INTEGER NOT NULL,
    position_id INTEGER NOT NULL,
    trend TEXT NOT NULL,
    iv_regime TEXT NOT NULL,
    PRIMARY KEY (run_id, position_id)
);
CREATE TABLE IF NOT EXISTS fills (
    run_id INTEGER NOT NULL,
    position_id INTEGER NOT NULL,
    instrument_key TEXT NOT NULL,
    observed_pct REAL NOT NULL,
    modelled_pct REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_meta (
    run_id INTEGER NOT NULL,
    position_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    idea_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (run_id, position_id)
);
CREATE INDEX IF NOT EXISTS shadow_day ON shadow_meta(run_id, day);
"""


def code_fingerprint(root: Path = PACKAGE_ROOT) -> str:
    """Hash of every module that can affect a trading decision."""
    digest = hashlib.sha256()
    for package in DECISION_PACKAGES:
        for path in sorted((root / package).rglob("*.py")):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def config_fingerprints(config_dir: Path) -> dict[str, str]:
    return {
        name: hashlib.sha256((config_dir / name).read_bytes()).hexdigest()[:16]
        for name in LOCKED_CONFIG_FILES
    }


@dataclass
class Run:
    id: int
    name: str
    version: int
    role: str
    status: str
    started_at: str
    ended_at: str | None
    end_reason: str | None
    promoted_at: str | None
    min_score: float
    disabled_profiles: list[str]
    fingerprint: dict
    backtest_hash: str
    gates: dict
    report_id: str | None
    holdout_id: str | None
    baseline: dict | None


class ForwardStore:
    def __init__(self, data_dir: Path):
        self.folder = Path(data_dir) / "forward"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "forward.sqlite"
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _decode(row: sqlite3.Row) -> Run:
        values = dict(row)
        for key in ("disabled_profiles", "fingerprint", "gates"):
            values[key] = json.loads(values[key])
        values["baseline"] = json.loads(values["baseline"]) if values["baseline"] else None
        return Run(**values)

    def runs(self, status: str | None = None) -> list[Run]:
        query, args = "SELECT * FROM runs ORDER BY id DESC", ()
        if status:
            query, args = "SELECT * FROM runs WHERE status = ? ORDER BY id", (status,)
        with self._connect() as connection:
            return [self._decode(row) for row in connection.execute(query, args).fetchall()]

    def run(self, run_id: int) -> Run | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return None if row is None else self._decode(row)

    def insert(self, **fields) -> Run:
        names = ", ".join(fields)
        marks = ", ".join(f":{name}" for name in fields)
        with self._connect() as connection:
            cursor = connection.execute(f"INSERT INTO runs ({names}) VALUES ({marks})", fields)
            run_id = cursor.lastrowid
        return self.run(run_id)

    def update(self, run_id: int, **fields) -> None:
        assignments = ", ".join(f"{name} = :{name}" for name in fields)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE runs SET {assignments} WHERE id = :run_id", fields | {"run_id": run_id}
            )

    def add_label(self, run_id: int, position_id: int, trend: str, iv_regime: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO labels VALUES (?, ?, ?, ?)",
                (run_id, position_id, trend, iv_regime),
            )

    def labels(self, run_id: int) -> dict[int, dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT position_id, trend, iv_regime FROM labels WHERE run_id = ?", (run_id,)
            ).fetchall()
        return {r["position_id"]: {"trend": r["trend"], "iv_regime": r["iv_regime"]} for r in rows}

    def add_fills(self, rows: list[tuple]) -> None:
        with self._connect() as connection:
            connection.executemany("INSERT INTO fills VALUES (?, ?, ?, ?, ?)", rows)

    def slippage(self, run_id: int) -> tuple[float | None, int]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT AVG(observed_pct) o, AVG(modelled_pct) m, COUNT(*) n FROM fills"
                " WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if not row["n"] or not row["m"]:
            return None, row["n"]
        return float(row["o"] / row["m"]), row["n"]

    def shadow_seen(self, run_id: int, day: str) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT idea_id FROM shadow_meta WHERE run_id = ? AND day = ?", (run_id, day)
            ).fetchall()
        return {row["idea_id"] for row in rows}

    def add_shadow(
        self, run_id: int, position_id: int, day: str, key: str, stage: str, reason: str
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO shadow_meta VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, position_id, day, key, stage, reason),
            )

    def shadow_stages(self, run_id: int) -> dict[int, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT position_id, stage FROM shadow_meta WHERE run_id = ?", (run_id,)
            ).fetchall()
        return {row["position_id"]: row["stage"] for row in rows}


@dataclass
class Live:
    scoring: object
    risk: object
    costs: object
    events: object
    gates: GatesConfig
    spread: object
    ai: AiService | None


class ForwardManager:
    def __init__(self, settings: Settings, store: SnapshotStore):
        self.settings = settings
        self.market_store = store
        self.store = ForwardStore(settings.data_dir)
        self.registry = Registry(settings.data_dir)
        self._code = code_fingerprint()
        self._live: Live | None = None
        self._live_key: dict | None = None

    # ---- Configuration -------------------------------------------------------------------

    def fingerprint(self) -> dict:
        return {"code": self._code} | config_fingerprints(self.settings.config_dir)

    def _load_live(self) -> Live:
        """Read configuration from disk, so an edit takes effect without a restart."""
        current = self.fingerprint()
        if self._live is None or current != self._live_key:
            directory = self.settings.config_dir
            ai_config = load_ai_config(directory)
            ai_on = self.settings.ai_enabled and ai_config.enabled
            self._live = Live(
                scoring=load_scoring_config(directory),
                risk=load_risk_config(directory),
                costs=load_cost_model(directory),
                events=None,
                gates=load_gates(directory),
                spread=load_backtest_config(directory).spread,
                ai=build_ai_service(ai_config, self.settings.data_dir) if ai_on else None,
            )
            self._live_key = current
        self._live.events = load_live_calendar(
            self.settings.config_dir, self.settings.data_dir, datetime.now(IST).date()
        )
        return self._live

    # ---- Run lifecycle -------------------------------------------------------------------

    def start(
        self,
        name: str,
        now: datetime,
        role: str = CHALLENGER,
        min_score: float | None = None,
        disabled_profiles: tuple[str, ...] = (),
        report_id: str | None = None,
        holdout_id: str | None = None,
        version: int = 1,
    ) -> Run:
        live = self._load_live()
        if role == CHAMPION and any(r.role == CHAMPION for r in self.store.runs(ACTIVE)):
            raise ValueError("A champion run is already active. Start this one as a challenger.")
        run = self.store.insert(
            name=name,
            version=version,
            role=role,
            status=ACTIVE,
            started_at=now.isoformat(),
            min_score=live.risk.paper.min_score if min_score is None else min_score,
            disabled_profiles=json.dumps(list(disabled_profiles)),
            fingerprint=json.dumps(self.fingerprint()),
            backtest_hash=config_hash(self.settings.config_dir),
            gates=live.gates.model_dump_json(),
            report_id=None,
            holdout_id=None,
            baseline=None,
        )
        if report_id or holdout_id:
            self.attach(run.id, report_id, holdout_id)
        return self.store.run(run.id)

    def attach(self, run_id: int, report_id: str | None, holdout_id: str | None) -> Run:
        """Link backtest evidence. This never changes how the run trades."""
        run = self.store.run(run_id)
        if run is None:
            raise ValueError(f"No forward run with id {run_id}")
        changes: dict = {}
        if report_id:
            report = self.registry.read_report(report_id)
            if report is None:
                raise ValueError(f"No backtest report named {report_id}")
            if report["kind"] != "walk_forward":
                raise ValueError("The expected results must come from a walk-forward report.")
            baseline = extract_baseline(report)
            changes |= {
                "report_id": report_id,
                "baseline": json.dumps(baseline) if baseline else None,
            }
        if holdout_id:
            if (self.registry.read_report(holdout_id) or {}).get("kind") != "holdout":
                raise ValueError(f"No holdout report named {holdout_id}")
            changes["holdout_id"] = holdout_id
        if changes:
            self.store.update(run_id, **changes)
        return self.store.run(run_id)

    def _paths(self, run: Run) -> tuple[Path, Path]:
        folder = self.store.folder
        return folder / f"run-{run.id}.sqlite", folder / f"run-{run.id}-shadow.sqlite"

    def _broker(self, run: Run, live: Live) -> tuple[PaperBroker, object]:
        risk = live.risk.model_copy(deep=True)
        risk.paper.min_score = run.min_score
        risk.paper.disabled_profiles = list(run.disabled_profiles)
        risk.paper.auto_trade = True
        return PaperBroker(Ledger(self._paths(run)[0]), risk, live.costs), risk

    def _shadow_broker(self, run: Run, live: Live) -> PaperBroker:
        risk = live.risk.model_copy(deep=True)
        risk.max_daily_loss_pct = UNLIMITED_PCT
        return PaperBroker(Ledger(self._paths(run)[1]), risk, live.costs)

    def stop(
        self,
        run: Run,
        now: datetime,
        reason: str,
        chains: dict[str, pd.DataFrame] | None = None,
        status: str = ENDED,
    ) -> None:
        """End a run and close its paper positions at the best available prices."""
        live = self._load_live()
        self._broker(run, live)[0].flatten(now, chains or {})
        self._shadow_broker(run, live).flatten(now, chains or {})
        self.store.update(run.id, status=status, ended_at=now.isoformat(), end_reason=reason)
        log.info("Forward run %s v%s stopped: %s", run.name, run.version, reason)

    # ---- Cycle ---------------------------------------------------------------------------

    def on_cycle(
        self, raw_chains: dict[str, pd.DataFrame], contracts: pd.DataFrame, now: datetime
    ) -> dict[int, CycleResult]:
        """Process every active run against one market snapshot."""
        results: dict[int, CycleResult] = {}
        runs = self.store.runs(ACTIVE)
        if not runs or not raw_chains:
            return results
        live = self._load_live()
        chains = prepare_chains(raw_chains, contracts, now, live.scoring.risk_free_rate)
        current = self.fingerprint()
        for run in runs:
            if run.fingerprint != current:
                changed = sorted(k for k in current if run.fingerprint.get(k) != current[k])
                self.stop(run, now, f"Locked inputs changed: {', '.join(changed)}", chains)
                run = self.start(
                    run.name,
                    now,
                    run.role,
                    run.min_score,
                    tuple(run.disabled_profiles),
                    version=run.version + 1,
                )
            try:
                results[run.id] = self._process(run, live, raw_chains, chains, contracts, now)
            except Exception:  # One broken run must not stop the others.
                log.exception("Forward run %s failed this cycle", run.id)
        return results

    def after_close(self, now: datetime) -> None:
        """Settle expired positions once the market has closed."""
        runs = self.store.runs(ACTIVE)
        if runs:
            live = self._load_live()
            for run in runs:
                self._broker(run, live)[0].step(now, {})
                self._shadow_broker(run, live).step(now, {})

    def _process(self, run, live, raw_chains, chains, contracts, now) -> CycleResult:
        broker, risk = self._broker(run, live)
        deps = CycleDeps(
            store=self.market_store,
            broker=broker,
            scoring=live.scoring,
            risk=risk,
            costs=live.costs,
            events=live.events,
            ai=live.ai,
            publish=False,
        )
        result = run_cycle(deps, raw_chains, contracts, now)
        self._record_fills(run, broker, result, chains, live)
        for position_id in result.submitted:
            position = next(p for p in broker.ledger.positions(("pending",)) if p.id == position_id)
            trend, iv_regime = regime_label(result.contexts[position.symbol])
            self.store.add_label(run.id, position_id, trend, iv_regime)
        self._track_shadow(run, live, result, chains, now)
        self._enforce_drawdown(run, broker, chains, now)
        self._check_promotion(run, broker, now)
        return result

    def _record_fills(self, run, broker, result, chains, live) -> None:
        """Compare the spread actually paid with the spread backtests assume."""
        opened = set(result.report.opened)
        if not opened:
            return
        quotes = {}
        for chain in chains.values():
            columns = [
                "instrument_key",
                "bid",
                "ask",
                "mid",
                "strike",
                "spot",
                "volume",
                "lot_size",
            ]
            quotes.update(chain[columns].set_index("instrument_key").to_dict("index"))
        rows = []
        for position in broker.ledger.positions((OPEN, EXITING, CLOSED)):
            if position.id not in opened:
                continue
            for leg in position.legs:
                quote = quotes.get(leg.instrument_key)
                if quote is None or not quote["mid"] > 0:
                    continue
                moneyness = abs(quote["strike"] / quote["spot"] - 1) * 100
                lots = (quote["volume"] or 0.0) / quote["lot_size"]
                modelled = float(
                    spread_percent(np.array([moneyness]), np.array([lots]), live.spread)[0]
                )
                observed = (quote["ask"] - quote["bid"]) / quote["mid"] * 100
                rows.append((run.id, position.id, leg.instrument_key, observed, modelled))
        if rows:
            self.store.add_fills(rows)

    def _track_shadow(self, run, live, result, chains, now) -> None:
        shadow = self._shadow_broker(run, live)
        shadow.step(now, chains)
        day = now.date().isoformat()
        seen = self.store.shadow_seen(run.id, day)
        picks = select(result.ideas, seen, len(seen), live.gates.shadow)
        for idea, stage, reason in picks:
            position_id = shadow.submit(as_one_lot(idea), now)
            self.store.add_shadow(run.id, position_id, day, idea_id(idea), stage, reason)

    def _enforce_drawdown(self, run, broker, chains, now) -> None:
        limit_pct = GatesConfig.model_validate(run.gates).max_drawdown_pct
        series = [pnl for _, pnl in broker.ledger.equity_series(EQUITY_POINTS * 100)]
        drawdown = max_drawdown(np.array(series, float))
        if drawdown / broker.risk.capital * 100 >= limit_pct:
            self.stop(run, now, f"Drawdown limit of {limit_pct:g}% reached", chains, HALTED)

    def _check_promotion(self, run, broker, now) -> None:
        if run.promoted_at is None and self.store.run(run.id).status == ACTIVE:
            if all(gate["passed"] for gate in self._evidence(run, broker, now)["gates"]):
                self.store.update(run.id, promoted_at=now.isoformat())
                log.info("Forward run %s v%s is a live-trading candidate", run.name, run.version)

    # ---- Reporting -----------------------------------------------------------------------

    def _evidence(self, run: Run, broker: PaperBroker, now: datetime) -> dict:
        ledger = broker.ledger
        config = GatesConfig.model_validate(run.gates)
        capital = broker.risk.capital
        trades = trades_frame(ledger, self.store.labels(run.id))
        equity = ledger.equity_series(EQUITY_POINTS * 100)
        drawdown = max_drawdown(np.array([pnl for _, pnl in equity], float))
        ratio, fills = self.store.slippage(run.id)
        drift = drift_checks(run.baseline, trades, drawdown, ratio, fills, config)
        status = {check["key"]: check["status"] for check in drift}
        report = self.registry.read_report(run.report_id) if run.report_id else None
        holdout = self.registry.read_report(run.holdout_id) if run.holdout_id else None
        end = datetime.fromisoformat(run.ended_at) if run.ended_at else now
        weeks = (end - datetime.fromisoformat(run.started_at)).total_seconds() / 604800
        regimes = set(trades["iv_regime"]) - {"unknown"} if not trades.empty else set()
        expiries = {p.front_expiry for p in ledger.positions((CLOSED,))}
        gates = evaluate_gates(
            GateInputs(
                backtest_verdict=report.get("verdict") if report else None,
                holdout_verdict=holdout.get("verdict") if holdout else None,
                reports_match_config=bool(report and holdout)
                and report.get("config_hash") == run.backtest_hash
                and holdout.get("config_hash") == run.backtest_hash,
                weeks=weeks,
                trades=len(trades),
                volatility_regimes=len(regimes),
                expiries=len(expiries),
                expectancy=float(trades["net_pnl"].mean()) if not trades.empty else None,
                expectancy_status=status["expectancy"],
                drawdown_pct=drawdown / capital * 100,
                slippage_ratio=ratio,
            ),
            config,
        )
        return {
            "trades": trades,
            "equity": equity,
            "drawdown": drawdown,
            "drift": drift,
            "gates": gates,
            "slippage_ratio": ratio,
            "fills": fills,
            "weeks": weeks,
        }

    def _shadow_summary(self, run: Run, live: Live) -> list[dict]:
        shadow = self._shadow_broker(run, live).ledger
        stages = self.store.shadow_stages(run.id)
        tail = 0.05
        closed = shadow.positions((CLOSED,))
        frame = trades_frame(shadow, {})
        if not frame.empty:
            frame = frame.assign(stage=[stages.get(p.id, "unknown") for p in closed])
        unfilled = [stages.get(p.id) for p in shadow.positions((CANCELLED,))]
        live_stages = [stages.get(p.id) for p in shadow.positions(LIVE_STATUSES)]
        rows = []
        for stage in STAGES:
            subset = frame[frame["stage"] == stage] if not frame.empty else frame
            rows.append(
                {"stage": stage}
                | trade_stats(subset, tail)
                | {"unfilled": unfilled.count(stage), "following": live_stages.count(stage)}
            )
        return rows

    @staticmethod
    def _ai_value(shadow: list[dict], minimum: int) -> dict:
        by_stage = {row["stage"]: row for row in shadow}
        approved, vetoed = by_stage["ai_approved"], by_stage["ai_veto"]
        enough = approved["trades"] >= minimum and vetoed["trades"] >= minimum
        return {
            "approved_trades": approved["trades"],
            "vetoed_trades": vetoed["trades"],
            "approved_expectancy": approved.get("expectancy"),
            "vetoed_expectancy": vetoed.get("expectancy"),
            "enough_data": enough,
            "benefit_per_trade": approved["expectancy"] - vetoed["expectancy"] if enough else None,
        }

    def overview(self, now: datetime) -> dict:
        live = self._load_live()
        current = self.fingerprint()
        rows = []
        for run in self.store.runs():
            broker = self._broker(run, live)[0]
            evidence = self._evidence(run, broker, now)
            trades = evidence["trades"]
            shadow = self._shadow_summary(run, live)
            config = GatesConfig.model_validate(run.gates)
            daily = daily_pnl_series(broker.ledger)
            equity = evidence["equity"][-EQUITY_POINTS:]
            rows.append(
                asdict(run)
                | {
                    "locked_inputs_current": run.fingerprint == current,
                    "weeks": evidence["weeks"],
                    "stats": trade_stats(trades, 0.05) | daily_stats(daily, broker.risk.capital),
                    "drawdown": evidence["drawdown"],
                    "gates": evidence["gates"],
                    "gate_limits": run.gates,
                    "drift": evidence["drift"],
                    "slippage_ratio": evidence["slippage_ratio"],
                    "fills": evidence["fills"],
                    "shadow": shadow,
                    "ai_value": self._ai_value(shadow, config.min_drift_samples),
                    "equity": [{"ts": ts, "pnl": pnl} for ts, pnl in equity],
                    "live_positions": len(broker.ledger.positions(LIVE_STATUSES)),
                }
            )
        return clean({"runs": rows})

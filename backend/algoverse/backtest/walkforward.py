"""Walk-forward testing: tune on past data, then measure only on the following unseen window."""

from __future__ import annotations

import itertools
import secrets
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from algoverse.backtest.config import BacktestConfig, config_hash, load_backtest_config
from algoverse.backtest.dataview import History
from algoverse.backtest.engine import ReplayResult, ReplaySpec, Services, reference_series, replay
from algoverse.backtest.registry import Registry
from algoverse.backtest.report import daily_stats, date_range, summarize
from algoverse.backtest.robustness import peak_check
from algoverse.data.store import SnapshotStore
from algoverse.evaluation.config import load_event_calendar, load_scoring_config
from algoverse.risk.config import load_risk_config
from algoverse.risk.costs import load_cost_model

_WORKER_CACHE: dict[tuple[str, str], tuple] = {}


def load_services(config_dir: Path) -> Services:
    return Services(
        backtest=load_backtest_config(config_dir),
        scoring=load_scoring_config(config_dir),
        risk=load_risk_config(config_dir),
        costs=load_cost_model(config_dir),
        events=load_event_calendar(config_dir),
    )


def _run_in_worker(task: tuple[str, str, ReplaySpec]) -> ReplayResult:
    data_dir, config_dir, spec = task
    key = (data_dir, config_dir)
    if key not in _WORKER_CACHE:
        store = SnapshotStore(Path(data_dir))
        _WORKER_CACHE[key] = (
            History(Path(data_dir)),
            load_services(Path(config_dir)),
            reference_series(store),
        )
    history, services, reference = _WORKER_CACHE[key]
    return replay(history, services, spec, reference)


def _run_all(
    specs: list[ReplaySpec],
    data_dir: Path,
    config_dir: Path,
    workers: int,
    progress: Callable[[int, int], None] | None = None,
) -> list[ReplayResult]:
    """Replay every spec, in order. `progress(done, total)` is called as replays finish."""
    tasks = [(str(data_dir), str(config_dir), spec) for spec in specs]
    results = []
    if workers <= 1 or len(tasks) <= 1:
        for task in tasks:
            results.append(_run_in_worker(task))
            if progress:
                progress(len(results), len(tasks))
        return results
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(_run_in_worker, tasks):
            results.append(result)
            if progress:
                progress(len(results), len(tasks))
    return results


@dataclass(frozen=True)
class Fold:
    train_start: date
    train_end: date
    test_start: date
    test_end: date


def build_folds(sessions: list[date], config: BacktestConfig) -> tuple[list[Fold], list[date]]:
    """Return rolling folds and the final holdout sessions, which no fold touches."""
    settings = config.walk_forward
    split = len(sessions) - settings.holdout_sessions
    usable, holdout = sessions[:split], sessions[split:]
    folds = []
    first_train = min(config.warmup_sessions, len(usable))
    train_end = first_train + settings.train_sessions
    while train_end + settings.test_sessions <= len(usable):
        train_start = train_end - settings.train_sessions
        test_end = train_end + settings.test_sessions
        folds.append(
            Fold(
                usable[train_start],
                usable[train_end - 1],
                usable[train_end],
                usable[test_end - 1],
            )
        )
        train_end += settings.test_sessions
    return folds, holdout


def parameter_grid(config: BacktestConfig) -> list[tuple[float, tuple[str, ...]]]:
    return [
        (score, tuple(disabled))
        for score, disabled in itertools.product(
            config.grid.min_score, config.grid.disabled_profiles
        )
    ]


def _sharpe(result: ReplayResult, capital: float) -> float | None:
    return daily_stats(result.daily_pnl, capital).get("sharpe")


def choose_best(
    results: list[ReplayResult], capital: float, min_trades: int
) -> ReplayResult | None:
    """Highest training Sharpe among runs with enough trades, or None to sit the fold out."""
    eligible = [
        (sharpe, result)
        for result in results
        if len(result.trades) >= min_trades
        and (sharpe := _sharpe(result, capital)) is not None
        and sharpe > 0
    ]
    return max(eligible, key=lambda pair: pair[0])[1] if eligible else None


def _log(registry, now, kind, digest, result, capital) -> None:
    spec = result.spec
    registry.record_trial(
        now,
        kind,
        digest,
        spec.start,
        spec.end,
        {
            "min_score": spec.min_score,
            "disabled_profiles": list(spec.disabled_profiles),
            "spread_multiplier": spec.spread_multiplier,
        },
        _sharpe(result, capital),
        float(result.trades["net_pnl"].sum()) if not result.trades.empty else 0.0,
        len(result.trades),
    )


def _stitch(results: list[ReplayResult]) -> tuple[pd.DataFrame, pd.Series]:
    frames = [r.trades for r in results if not r.trades.empty]
    trades = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    series = [r.daily_pnl for r in results if len(r.daily_pnl)]
    daily = pd.concat(series).sort_index() if series else pd.Series(dtype=float)
    return trades, daily


def size_text(count: int) -> str:
    return f"{count} setting{'s' if count != 1 else ''}"


def _report_id(kind: str, now: datetime) -> str:
    return f"{kind}-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def run_backtest(
    data_dir: Path,
    config_dir: Path,
    now: datetime,
    spec: ReplaySpec,
    kind: str = "backtest",
    note: Callable[[str], None] | None = None,
) -> dict:
    """One configuration over one window. Useful for inspection, not for judging an edge."""
    services = load_services(config_dir)
    digest = config_hash(config_dir)
    registry = Registry(data_dir)
    if note:
        note(f"Replaying {spec.start} to {spec.end}")
    result = _run_all([spec], data_dir, config_dir, 1)[0]
    _log(registry, now, kind, digest, result, services.risk.capital)
    summary = summarize(result.trades, result.daily_pnl, services.backtest, services.risk.capital)
    report = {
        "id": _report_id(kind, now),
        "kind": kind,
        "created_at": now,
        "config_hash": digest,
        "window": {"start": spec.start, "end": spec.end, "sessions": result.sessions},
        "params": asdict(spec),
        "unfilled": result.unfilled,
        "trials_to_date": registry.trial_count(),
        "summary": summary,
        "verdict": "single_run",
    }
    registry.save_report(report)
    return report


def run_walk_forward(
    data_dir: Path, config_dir: Path, now: datetime, note: Callable[[str], None] | None = None
) -> dict:
    services = load_services(config_dir)
    config = services.backtest
    settings = config.walk_forward
    capital = services.risk.capital
    digest = config_hash(config_dir)
    registry = Registry(data_dir)
    folds, holdout = build_folds(History(data_dir).sessions, config)
    if not folds:
        raise ValueError(
            f"Not enough history for one fold: need "
            f"{config.warmup_sessions + settings.train_sessions + settings.test_sessions} sessions "
            f"plus {settings.holdout_sessions} for the holdout."
        )
    grid = parameter_grid(config)
    train_specs = [
        ReplaySpec(fold.train_start, fold.train_end, score, disabled)
        for fold in folds
        for score, disabled in grid
    ]

    def stage(label: str):
        if note is None:
            return None

        def report(done: int, total: int) -> None:
            try:
                note(f"{label}: {done} of {total} replays", done=done, total=total)
            except TypeError:  # A plain note, such as the command line's print.
                note(f"{label}: {done} of {total} replays")

        return report

    if note:
        note(f"Training {size_text(len(grid))} on {len(folds)} folds")
    train_results = _run_all(train_specs, data_dir, config_dir, settings.workers, stage("Training"))
    for result in train_results:
        _log(registry, now, "walk_forward_train", digest, result, capital)

    fold_rows, test_specs, chosen_index = [], [], []
    size = len(grid)
    for index, fold in enumerate(folds):
        batch = train_results[index * size : (index + 1) * size]
        best = choose_best(batch, capital, settings.min_train_trades)
        row = {
            "train": [fold.train_start, fold.train_end],
            "test": [fold.test_start, fold.test_end],
        }
        if best is None:
            row |= {"chosen": None, "train_sharpe": None}
        else:
            row |= {
                "chosen": {
                    "min_score": best.spec.min_score,
                    "disabled_profiles": list(best.spec.disabled_profiles),
                },
                "train_sharpe": _sharpe(best, capital),
                "sensitivity": peak_check(batch, best, capital),
            }
            chosen_index.append(index)
            test_specs.append(
                ReplaySpec(
                    fold.test_start, fold.test_end, best.spec.min_score, best.spec.disabled_profiles
                )
            )
        fold_rows.append(row)

    stressed_specs = [replace(spec, spread_multiplier=2.0) for spec in test_specs]
    tests = _run_all(
        test_specs + stressed_specs,
        data_dir,
        config_dir,
        settings.workers,
        stage("Testing on unseen windows"),
    )
    normal, stressed = tests[: len(test_specs)], tests[len(test_specs) :]
    for result in tests:
        _log(registry, now, "walk_forward_test", digest, result, capital)
    for index, result in zip(chosen_index, normal, strict=True):
        fold_rows[index]["test_sharpe"] = _sharpe(result, capital)
        fold_rows[index]["test_net_pnl"] = float(
            result.trades["net_pnl"].sum() if not result.trades.empty else 0.0
        )
        fold_rows[index]["test_trades"] = len(result.trades)

    trades, daily = _stitch(normal)
    stressed_trades, stressed_daily = _stitch(stressed)
    summary = summarize(trades, daily, config, capital)
    peaks = [row["sensitivity"] for row in fold_rows if row.get("sensitivity")]
    sharp = sum(1 for peak in peaks if peak["sharp_peak"])
    if sharp:
        summary["warnings"].append(
            f"{sharp} of {len(peaks)} folds picked a setting whose neighbours performed much "
            "worse, which suggests curve-fitting."
        )
    idle = sum(1 for row in fold_rows if row["chosen"] is None)
    if idle:
        summary["warnings"].append(
            f"{idle} of {len(fold_rows)} folds had no setting with a positive training Sharpe."
        )
    report = {
        "id": _report_id("walk_forward", now),
        "kind": "walk_forward",
        "created_at": now,
        "config_hash": digest,
        "window": date_range(sorted(daily.index)) if len(daily) else {},
        "holdout": date_range(holdout),
        "folds": fold_rows,
        "grid_size": size,
        "trials_to_date": registry.trial_count(),
        "summary": summary,
        "double_slippage": {
            "net_pnl": float(stressed_trades["net_pnl"].sum())
            if not stressed_trades.empty
            else 0.0,
            "sharpe": daily_stats(stressed_daily, capital).get("sharpe"),
        },
        "verdict": _verdict(summary, config, sharp, stressed_trades),
    }
    registry.save_report(report)
    return report


def _verdict(summary: dict, config: BacktestConfig, sharp: int, stressed: pd.DataFrame) -> str:
    overall = summary["overall"]
    if overall.get("trades", 0) < config.min_trades_for_confidence:
        return "insufficient_evidence"
    if overall["net_pnl"] <= 0 or stressed.empty or stressed["net_pnl"].sum() <= 0 or sharp:
        return "failed"
    return "passed_walk_forward"


def run_holdout(
    data_dir: Path,
    config_dir: Path,
    now: datetime,
    min_score: float,
    disabled_profiles: tuple[str, ...] = (),
    allow_reuse: bool = False,
) -> dict:
    """Spend the final holdout on one configuration. Reuse is refused by default."""
    services = load_services(config_dir)
    config = services.backtest
    registry = Registry(data_dir)
    _, holdout = build_folds(History(data_dir).sessions, config)
    if not holdout:
        raise ValueError("No holdout sessions are configured or available.")
    uses = registry.holdout_uses(holdout[0], holdout[-1])
    if uses and not allow_reuse:
        raise PermissionError(
            f"This holdout was already used {uses} time(s). Re-testing against it makes it "
            "part of the tuning data."
        )
    spec = ReplaySpec(holdout[0], holdout[-1], min_score, disabled_profiles)
    report = run_backtest(data_dir, config_dir, now, spec, kind="holdout")
    registry.record_holdout(now, holdout[0], holdout[-1], report["id"])
    report["holdout_uses_before"] = uses
    net = report["summary"]["overall"].get("net_pnl", 0.0)
    report["verdict"] = "passed_holdout" if net > 0 else "failed"
    registry.save_report(report)
    return report

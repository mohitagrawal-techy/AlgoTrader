"""Backtests strategy definitions on a training period and a later, separate test period."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from algoverse.backtest.config import BacktestConfig, config_hash
from algoverse.backtest.dataview import History
from algoverse.backtest.engine import ReplaySpec, reference_series, replay
from algoverse.backtest.intraday import replay_intraday
from algoverse.backtest.progress import Poller, SessionCounter
from algoverse.backtest.registry import Registry
from algoverse.backtest.report import daily_stats, trade_stats
from algoverse.backtest.walkforward import load_services
from algoverse.dashboard import clean
from algoverse.data.candles import MINUTE
from algoverse.data.store import SnapshotStore
from algoverse.lab.evidence import luck_adjusted_floor
from algoverse.lab.spec import (
    DECIDE_MINUTES,
    NO_CONTRACTS,
    CustomSet,
    StrategySpec,
    apply_specs,
    signature,
)

HELD_UP = "held_up"
UNPROVEN = "unproven"
FAILS_2X = "fails_at_2x_slippage"
FAILED = "failed"
TOO_FEW = "too_few_trades"
VERDICT_ORDER = {HELD_UP: 0, UNPROVEN: 1, FAILS_2X: 2, FAILED: 3, TOO_FEW: 4}
TOP_REASONS = 5
_CACHE: dict[tuple[str, str], tuple] = {}


@dataclass(frozen=True)
class Windows:
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    train_sessions: int
    test_sessions: int


def split_windows(sessions: list[date], config: BacktestConfig) -> Windows | None:
    """Earlier sessions for tuning and the final share for testing, after the warm-up."""
    usable = sessions[config.warmup_sessions :]
    if len(usable) < config.lab.min_sessions:
        return None
    cut = int(len(usable) * (1 - config.lab.test_share))
    return Windows(usable[0], usable[cut - 1], usable[cut], usable[-1], cut, len(usable) - cut)


def sessions_needed(config: BacktestConfig) -> int:
    return config.warmup_sessions + config.lab.min_sessions


def normalize_reason(reason: str) -> str:
    """Group reasons that differ only in strikes or figures."""
    text = reason.split(": ", 1)[1] if reason[:1].isdigit() and ": " in reason else reason
    if not text.startswith("entry rule"):
        text = text.split(":")[0]
    return re.sub(r"\d[\d,.]*%?", "N", text).strip()


def _minutes(data_dir: str, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """One-minute index candles per underlying, read once per worker."""
    key = ("minutes", data_dir)
    if key not in _CACHE:
        store = SnapshotStore(Path(data_dir))
        _CACHE[key] = {symbol: store.read_candles(symbol, MINUTE) for symbol in symbols}
    return _CACHE[key]


def _run(task: tuple) -> dict:
    """One replay. An optional seventh item, (counts, key), records sessions finished so far."""
    data_dir, config_dir, spec_json, start, end, multiplier, *extra = task
    tracker = extra[0] if extra else None
    days: set = set()
    key = (data_dir, config_dir)
    if key not in _CACHE:
        _CACHE[key] = (
            History(Path(data_dir)),
            load_services(Path(config_dir)),
            reference_series(SnapshotStore(Path(data_dir))),
        )
    history, base, reference = _CACHE[key]
    spec = StrategySpec.model_validate_json(spec_json)
    services = apply_specs(base, [spec])
    custom = CustomSet([spec])
    outcomes: Counter = Counter()

    def on_cycle(result) -> None:
        if tracker is not None and result.contexts:
            day = next(iter(result.contexts.values())).now.date()
            if day not in days:
                days.add(day)
                counts, key = tracker
                counts[key] = len(days)
        for idea in result.ideas:
            if idea.tradable:
                outcomes["signal"] += 1
            else:
                reasons = idea.rejections or idea.risk.reasons
                outcomes[normalize_reason(reasons[0])] += 1

    window = ReplaySpec(start, end, 0.0, (), multiplier)
    if spec.decide_every == "day":
        result = replay(history, services, window, reference, custom=custom, on_cycle=on_cycle)
    else:
        result = replay_intraday(
            history,
            services,
            window,
            _minutes(data_dir, services.backtest.symbols),
            DECIDE_MINUTES[spec.decide_every],
            reference,
            custom=custom,
            on_cycle=on_cycle,
        )
    signals = outcomes.pop("signal", 0)
    if custom.no_contracts[spec.id]:
        outcomes[NO_CONTRACTS] = custom.no_contracts[spec.id]
    capital = services.risk.capital
    daily = result.daily_pnl
    return {
        "stats": trade_stats(result.trades, services.backtest.tail_share)
        | daily_stats(daily, capital),
        "signals": signals,
        "blocked": dict(outcomes.most_common(TOP_REASONS)),
        "unfilled": result.unfilled,
        "pnls": ([] if result.trades.empty else [float(pnl) for pnl in result.trades["net_pnl"]]),
        "daily": {str(day): float(pnl) for day, pnl in zip(daily.index, daily, strict=True)},
        "trades": result.details,
        "equity": [
            {"day": day, "pnl": float(pnl)}
            for day, pnl in zip(daily.index, daily.cumsum().to_numpy(float), strict=True)
        ],
    }


def _summary(run: dict) -> dict:
    keys = (
        "trades",
        "net_pnl",
        "costs",
        "win_rate",
        "expectancy",
        "profit_factor",
        "sharpe",
        "max_drawdown",
        "worst_day",
        "average_holding_days",
    )
    stats = run["stats"]
    return {key: stats.get(key) for key in keys} | {
        "trades": stats.get("trades", 0),
        "net_pnl": stats.get("net_pnl", 0.0),
        "signals": run["signals"],
        "blocked": run["blocked"],
        "unfilled": run["unfilled"],
    }


def verdict(test: dict, doubled: dict, min_trades: int, floor: float | None = None) -> str:
    """`floor` is the luck-adjusted profit per trade. A profit below it may be chance."""
    if test["trades"] < min_trades:
        return TOO_FEW
    if test["net_pnl"] <= 0:
        return FAILED
    if doubled["net_pnl"] <= 0:
        return FAILS_2X
    return UNPROVEN if floor is not None and floor <= 0 else HELD_UP


def rank_key(result: dict | None) -> tuple:
    if result is None:
        return (len(VERDICT_ORDER), 0.0)
    return (VERDICT_ORDER[result["verdict"]], -(result["test"].get("sharpe") or -99.0))


def run_tasks(
    tasks: list[tuple],
    workers: int,
    sessions: list[date] | None = None,
    on_sessions: Callable[[int, int], None] | None = None,
    pool: ProcessPoolExecutor | None = None,
) -> list[dict]:
    """Run replay tasks in order, in parallel when allowed, reporting sessions replayed."""
    if on_sessions is None or sessions is None:
        if pool is not None:
            return list(pool.map(_run, tasks))
        if workers > 1 and len(tasks) > 1:
            with ProcessPoolExecutor(max_workers=min(workers, len(tasks))) as own:
                return list(own.map(_run, tasks))
        return [_run(task) for task in tasks]
    total = sum(sum(1 for day in sessions if task[3] <= day <= task[4]) for task in tasks)
    parallel = pool is not None or (workers > 1 and len(tasks) > 1)
    counter = SessionCounter(shared=parallel)
    tagged = [(*task, (counter.counts, index)) for index, task in enumerate(tasks)]
    try:
        with Poller(counter, total, on_sessions):
            if pool is not None:
                return list(pool.map(_run, tagged))
            if parallel:
                with ProcessPoolExecutor(max_workers=min(workers, len(tasks))) as own:
                    return list(own.map(_run, tagged))
            return [_run(task) for task in tagged]
    finally:
        counter.close()


def evaluate_specs(
    data_dir: Path,
    config_dir: Path,
    specs: list[StrategySpec],
    now: datetime,
    progress: Callable[[int, int, str], None] | None = None,
    trials: int | None = None,
    on_sessions: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """Three replays per strategy: training, test, and test at double the modelled spread.

    `trials` is how many strategies have been tried in all, which sets how strict the luck
    adjustment is.
    """
    services = load_services(config_dir)
    config = services.backtest
    windows = split_windows(History(data_dir).sessions, config)
    if windows is None:
        raise ValueError(
            f"At least {sessions_needed(config)} sessions of imported history are needed."
        )
    tasks = []
    for spec in specs:
        payload = spec.model_dump_json()
        base = (str(data_dir), str(config_dir), payload)
        tasks += [
            (*base, windows.train_start, windows.train_end, 1.0),
            (*base, windows.test_start, windows.test_end, 1.0),
            (*base, windows.test_start, windows.test_end, 2.0),
        ]
    registry = Registry(data_dir)
    digest = config_hash(config_dir)
    results = []

    def finish(spec: StrategySpec, train_run: dict, test_run: dict, doubled_run: dict) -> dict:
        train, test, doubled = _summary(train_run), _summary(test_run), _summary(doubled_run)
        evidence = luck_adjusted_floor(test_run["pnls"], trials or len(specs))
        for kind, start, end, summary, multiplier in (
            ("lab_train", windows.train_start, windows.train_end, train, 1.0),
            ("lab_test", windows.test_start, windows.test_end, test, 1.0),
        ):
            registry.record_trial(
                now,
                kind,
                digest,
                start,
                end,
                {"strategy": spec.id, "signature": signature(spec), "spread": multiplier},
                summary.get("sharpe"),
                summary["net_pnl"],
                summary["trades"],
            )
        return clean(
            {
                "strategy_id": spec.id,
                "signature": signature(spec),
                "evaluated_at": now,
                "config_hash": digest,
                "windows": windows.__dict__,
                "train": train,
                "test": test,
                "test_2x": {key: doubled.get(key) for key in ("trades", "net_pnl", "sharpe")},
                "evidence": evidence,
                "verdict": verdict(
                    test,
                    doubled,
                    config.lab.min_test_trades,
                    None if evidence is None else evidence["floor"],
                ),
                "equity": test_run["equity"],
                # Every trade and day of both windows, for the strategy's backtest page.
                "detail": {
                    "train": {"daily": train_run["daily"], "trades": train_run["trades"]},
                    "test": {"daily": test_run["daily"], "trades": test_run["trades"]},
                },
            }
        )

    runs = iter(run_tasks(tasks, config.lab.workers, History(data_dir).sessions, on_sessions))
    for index, spec in enumerate(specs):
        results.append(finish(spec, next(runs), next(runs), next(runs)))
        if progress:
            progress(index + 1, len(specs), spec.name)
    return results

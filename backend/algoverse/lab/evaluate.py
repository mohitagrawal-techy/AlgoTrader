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
from algoverse.backtest.report import daily_stats, exposure_stats, trade_stats
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
    with_budget,
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


def _load(data_dir: str, config_dir: str):
    key = (data_dir, config_dir)
    if key not in _CACHE:
        _CACHE[key] = (
            History(Path(data_dir)),
            load_services(Path(config_dir)),
            reference_series(SnapshotStore(Path(data_dir))),
        )
    return _CACHE[key]


def _run(task: tuple) -> dict:
    """One replay: (data, config, spec, start, end, spread multiplier[, window start[, tracker]]).

    The window start is set for a chunk of a longer window. The tracker, (counts, key),
    records how many sessions have been replayed so far."""
    data_dir, config_dir, spec_json, start, end, multiplier, *extra = task
    window_start = extra[0] if extra else None
    tracker = extra[1] if len(extra) > 1 else None
    days: set = set()
    history, base, reference = _load(data_dir, config_dir)
    spec = StrategySpec.model_validate_json(spec_json)
    services = with_budget(apply_specs(base, [spec]), spec)
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

    window = ReplaySpec(start, end, 0.0, (), multiplier, window_start=window_start)
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
            on_skip=lambda reasons: outcomes.update(normalize_reason(r) for r in reasons),
        )
    signals = outcomes.pop("signal", 0)
    if custom.no_contracts[spec.id]:
        outcomes[NO_CONTRACTS] = custom.no_contracts[spec.id]
    return _package(
        result.trades,
        result.daily_pnl,
        result.details,
        dict(result.unfilled),
        signals,
        outcomes,
        services,
    )


def _package(trades, daily, details, unfilled, signals, outcomes: Counter, services) -> dict:
    """A replay's results. `frame`, `daily_series` and `outcomes` let chunks be merged."""
    return {
        "stats": trade_stats(trades, services.backtest.tail_share)
        | daily_stats(daily, services.risk.capital)
        | exposure_stats(trades, daily.index, services.risk.capital),
        "signals": signals,
        "blocked": dict(outcomes.most_common(TOP_REASONS)),
        "unfilled": unfilled,
        "pnls": ([] if trades.empty else [float(pnl) for pnl in trades["net_pnl"]]),
        "daily": {str(day): float(pnl) for day, pnl in zip(daily.index, daily, strict=True)},
        "trades": details,
        "equity": [
            {"day": day, "pnl": float(pnl)}
            for day, pnl in zip(daily.index, daily.cumsum().to_numpy(float), strict=True)
        ],
        "frame": trades,
        "daily_series": daily,
        "outcomes": dict(outcomes),
    }


# ---- Splitting long windows across cores ---------------------------------------------------

# A chunk shorter than this spends more time warming up than replaying.
MIN_CHUNK_SESSIONS = 15


def _chunkable(spec_json: str) -> bool:
    """Intraday strategies that square off every day hold nothing overnight, so each session
    starts from the same state and a window can be replayed in independent pieces."""
    spec = StrategySpec.model_validate_json(spec_json)
    return spec.decide_every != "day" and spec.exits.square_off is not None


def _plan(tasks: list[tuple], workers: int) -> list[tuple[int, tuple]]:
    """Split long chunkable windows so every core has work. Returns (task index, sub-task)."""
    plain = list(enumerate(tasks))
    if workers <= 1:
        return plain
    history = _load(tasks[0][0], tasks[0][1])[0]
    windows: dict[int, list[date]] = {}
    for index, task in enumerate(tasks):
        if task[6] is None and _chunkable(task[2]):
            days = [d for d in history.sessions if task[3] <= d <= task[4]]
            windows[index] = [d for d in days if history.has_intraday(d)]
    total = sum(len(days) for days in windows.values())
    if not total:
        return plain
    # About two chunks per core, so one slow chunk does not leave the others idle.
    size = max(MIN_CHUNK_SESSIONS, -(-total // (workers * 2)))
    planned = []
    for index, task in enumerate(tasks):
        days = windows.get(index)
        if not days or len(days) < 2 * size:
            planned.append((index, task))
            continue
        pieces = [days[i : i + size] for i in range(0, len(days), size)]
        if len(pieces[-1]) < MIN_CHUNK_SESSIONS:
            tail = pieces.pop()
            pieces[-1] = pieces[-1] + tail
        for number, piece in enumerate(pieces):
            first = task[3] if number == 0 else piece[0]
            last = task[4] if number == len(pieces) - 1 else piece[-1]
            planned.append((index, (*task[:3], first, last, task[5], task[3])))
    return planned


def _merge(parts: list[dict], data_dir: str, config_dir: str, spec_json: str) -> dict:
    """One window's results from its chunks, which arrive in order. Stats are measured
    against the strategy's own budget, as a single run's are."""
    if len(parts) == 1:
        return parts[0]
    spec = StrategySpec.model_validate_json(spec_json)
    services = with_budget(apply_specs(_load(data_dir, config_dir)[1], [spec]), spec)
    frames = [p["frame"] for p in parts if not p["frame"].empty]
    trades = pd.concat(frames, ignore_index=True) if frames else parts[0]["frame"]
    series = [p["daily_series"] for p in parts if len(p["daily_series"])]
    daily = pd.concat(series) if series else parts[0]["daily_series"]
    outcomes: Counter = Counter()
    unfilled: Counter = Counter()
    for part in parts:
        outcomes.update(part["outcomes"])
        unfilled.update(part["unfilled"])
    return _package(
        trades,
        daily,
        [trade for part in parts for trade in part["trades"]],
        dict(unfilled),
        sum(part["signals"] for part in parts),
        outcomes,
        services,
    )


def _public(result: dict) -> dict:
    return {k: v for k, v in result.items() if k not in ("frame", "daily_series", "outcomes")}


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
        "max_drawdown_pct",
        "worst_day",
        "average_holding_days",
        "sortino",
        "annual_return",
        "total_return_pct",
        "average_win",
        "average_loss",
        "risk_reward",
        "longest_winning_streak",
        "longest_losing_streak",
        "recovery_factor",
        "exposure_pct",
        "capital_use_pct",
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
    """Run replay tasks, in parallel when allowed, and return their results in order.

    Long windows of strategies that square off daily are split into chunks so every core
    has work, and the chunks' results are merged back. `on_sessions(done, total)` reports
    sessions replayed."""
    tasks = [(*task[:6], task[6] if len(task) > 6 else None) for task in tasks]
    if not tasks:
        return []
    planned = _plan(tasks, workers)
    subtasks = [sub for _, sub in planned]
    parallel = pool is not None or (workers > 1 and len(subtasks) > 1)
    tracked = on_sessions is not None and sessions is not None
    counter = SessionCounter(shared=parallel) if tracked else None
    if counter is not None:
        subtasks = [(*sub, (counter.counts, i)) for i, sub in enumerate(subtasks)]

    def execute() -> list[dict]:
        if pool is not None:
            return list(pool.map(_run, subtasks))
        if parallel:
            with ProcessPoolExecutor(max_workers=min(workers, len(subtasks))) as own:
                return list(own.map(_run, subtasks))
        return [_run(sub) for sub in subtasks]

    try:
        if counter is not None:
            total = sum(sum(1 for d in sessions if t[3] <= d <= t[4]) for t in tasks)
            with Poller(counter, total, on_sessions):
                raw = execute()
        else:
            raw = execute()
    finally:
        if counter is not None:
            counter.close()
    grouped: dict[int, list[dict]] = {}
    for (index, _), result in zip(planned, raw, strict=True):
        grouped.setdefault(index, []).append(result)
    return [
        _public(_merge(grouped[index], tasks[index][0], tasks[index][1], tasks[index][2]))
        for index in range(len(tasks))
    ]


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
                # The budget it was tested with; a different one makes the result stale.
                "budget": spec.budget.model_dump(),
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

"""Searches a strategy's numeric settings together, then checks the best ones on unseen sessions.

The tuner changes one setting at a time. A search varies several at once with Optuna's grid,
random or Bayesian (TPE) sampler, scoring each setting on the design sessions only. The best
few are then run on the validation sessions, and the one that holds up best there is saved and
given the lab's final test, which reads the test period once.

Every setting backtested is logged as a trial, so the deflated Sharpe ratio counts the
search's full size. A sensitivity grid around the best setting shows whether its neighbours
also work: a lone peak among losers is luck, not an edge.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import optuna

from algoverse.backtest.config import BacktestConfig, config_hash
from algoverse.backtest.dataview import History
from algoverse.backtest.registry import Registry
from algoverse.lab.evaluate import _summary, run_tasks
from algoverse.lab.ladder import LadderWindows
from algoverse.lab.spec import UNIT_RANGES, StrategySpec, make_id, signature
from algoverse.lab.tuner import Change, apply_change

Method = Literal["grid", "random", "bayesian"]
METHODS = ("grid", "random", "bayesian")
SEED = 7
GRID_POINTS = 5
SENSITIVITY_POINTS = 5
# A setting needs this many design trades for its Sharpe to count; fewer scores FLOOR_SCORE.
MIN_DESIGN_TRADES = 8
FLOOR_SCORE = -5.0
MAX_PARAMS = 4
TRIAL_KEYS = ("trades", "net_pnl", "sharpe", "win_rate", "max_drawdown")

optuna.logging.set_verbosity(optuna.logging.WARNING)


@dataclass(frozen=True)
class Param:
    path: str
    label: str
    kind: Literal["int", "float"]
    low: float
    high: float
    step: float
    current: float

    def clip(self, value: float) -> float:
        value = min(max(value, self.low), self.high)
        if self.kind == "int":
            return int(round(value))
        steps = round((value - self.low) / self.step)
        return round(self.low + steps * self.step, 4)

    def grid(self, points: int) -> list[float]:
        if self.kind == "int":
            span = int(self.high - self.low)
            count = min(points, span + 1)
            values = {int(round(self.low + span * i / max(count - 1, 1))) for i in range(count)}
        else:
            values = {
                self.clip(self.low + (self.high - self.low) * i / max(points - 1, 1))
                for i in range(points)
            }
        return sorted(values | {self.clip(self.current)})


def _ratio_range(value: float, low_share: float, high_share: float, bounds: tuple) -> tuple:
    low, high = sorted((value * low_share, value * high_share))
    return max(low, bounds[0]), min(high, bounds[1])


def _float_param(path, label, value, low, high, step=None) -> Param | None:
    if high - low <= 0:
        return None
    step = step or _nice_step((high - low) / 20)
    low = math.floor(low / step) * step
    high = math.ceil(high / step) * step
    return Param(path, label, "float", round(low, 4), round(high, 4), step, float(value))


def _nice_step(raw: float) -> float:
    if raw <= 0:
        return 0.01
    power = 10 ** math.floor(math.log10(raw))
    return next(m * power for m in (1, 2, 5, 10) if m * power >= raw)


def search_space(spec: StrategySpec) -> list[Param]:
    """The numbers in a strategy a search may vary, each with a range around its value."""
    exits, entry = spec.exits, spec.entry
    found: list[Param | None] = []
    for name, label, unit in (
        ("target_frac", "Profit target", exits.target_unit),
        ("stop_frac", "Stop loss", exits.stop_unit),
    ):
        value = getattr(exits, name)
        low, high = _ratio_range(value, 0.4, 2.5, UNIT_RANGES[unit])
        found.append(_float_param(f"exits.{name}", f"{label} ({unit})", value, low, high))
    if exits.trail is not None:
        low, high = _ratio_range(exits.trail, 0.4, 2.5, UNIT_RANGES[exits.trail_unit])
        found.append(_float_param("exits.trail", "Trailing stop", exits.trail, low, high))
    for index, leg in enumerate(spec.legs):
        name = f"Leg {index + 1} {leg.action} {leg.option_type}"
        if leg.select == "delta":
            found.append(
                _float_param(
                    f"legs.{index}.value",
                    f"{name} delta",
                    leg.value,
                    max(0.05, leg.value - 0.15),
                    min(0.95, leg.value + 0.15),
                    0.01,
                )
            )
        elif leg.select == "atm_offset":
            low, high = max(-20, leg.value - 3), min(20, leg.value + 3)
            found.append(
                Param(
                    f"legs.{index}.value",
                    f"{name} strikes from ATM",
                    "int",
                    low,
                    high,
                    1,
                    leg.value,
                )
            )
        else:
            low, high = _ratio_range(leg.value, 0.5, 2.0, (0.5, 5000))
            found.append(
                _float_param(f"legs.{index}.value", f"{name} premium", leg.value, low, high)
            )
    found.append(
        Param(
            "entry.dte_max",
            "Most days to expiry at entry",
            "int",
            max(entry.dte_min, entry.dte_max - 7, 1),
            min(45, entry.dte_max + 7),
            1,
            entry.dte_max,
        )
    )
    if entry.dte_min - 1 > exits.exit_dte or exits.exit_dte > 0:
        found.append(
            Param(
                "exits.exit_dte",
                "Days before expiry to exit",
                "int",
                0,
                min(10, max(entry.dte_min - 1, exits.exit_dte)),
                1,
                exits.exit_dte,
            )
        )
    for index, condition in enumerate(entry.conditions):
        if condition.value:
            low, high = _ratio_range(condition.value, 0.5, 1.5, (-1e9, 1e9))
            found.append(
                _float_param(
                    f"entry.conditions.{index}.value",
                    f"{condition.signal} {condition.op}",
                    condition.value,
                    low,
                    high,
                )
            )
    for section, rules in (("entry.rules", entry.rules), ("exits.rules", exits.rules)):
        for index, rule in enumerate(rules):
            if rule.type != "compare":
                continue
            where = "Entry" if section.startswith("entry") else "Exit"
            for side in ("left", "right"):
                operand = getattr(rule, side)
                if operand.kind == "value" and operand.value:
                    low, high = _ratio_range(operand.value, 0.7, 1.3, (-1e9, 1e9))
                    found.append(
                        _float_param(
                            f"{section}.{index}.{side}.value",
                            f"{where} rule {index + 1} level",
                            operand.value,
                            low,
                            high,
                        )
                    )
                for name, value in operand.params.items():
                    if float(value).is_integer() and value >= 2:
                        found.append(
                            Param(
                                f"{section}.{index}.{side}.params.{name}",
                                f"{where} rule {index + 1} {operand.name} {name}",
                                "int",
                                max(2, int(value * 0.5)),
                                int(value * 2),
                                1,
                                value,
                            )
                        )
    return [param for param in found if param is not None and param.high > param.low]


def apply_values(spec: StrategySpec, values: dict[str, float]) -> StrategySpec | None:
    """The strategy with each path set to its value, or None if the mix is not valid."""
    current = spec
    for path, value in values.items():
        child, _ = apply_change(current, Change(path, value))
        if child is None:
            # Unchanged settings are fine; anything else is an invalid combination.
            data = current.model_dump(mode="json")
            holder = data
            for token in path.split("."):
                holder = holder[int(token)] if token.isdigit() else holder.get(token)
                if holder is None:
                    return None
            if holder != value:
                return None
            continue
        current = child
    return current


def describe(values: dict[str, float], params: list[Param]) -> str:
    labels = {param.path: param.label for param in params}
    return ", ".join(f"{labels.get(path, path)} {value:g}" for path, value in values.items())


def _grid(params: list[Param], budget: int) -> list[dict[str, float]]:
    """Every combination of evenly spaced values, with as many per setting as the budget allows."""
    points = min(GRID_POINTS, max(2, int(budget ** (1 / len(params)))))
    combos: list[dict[str, float]] = [{}]
    for param in params:
        combos = [combo | {param.path: value} for combo in combos for value in param.grid(points)]
    return combos[:budget]


def _sampler(method: Method, budget: int):
    if method == "bayesian":
        return optuna.samplers.TPESampler(seed=SEED, n_startup_trials=min(10, budget // 3))
    # A grid is queued trial by trial (Optuna's grid sampler needs its own optimise loop), so
    # its sampler only matters if the queue runs out.
    return optuna.samplers.RandomSampler(seed=SEED)


def _distribution(param: Param):
    if param.kind == "int":
        return optuna.distributions.IntDistribution(int(param.low), int(param.high))
    return optuna.distributions.FloatDistribution(param.low, param.high, step=param.step)


def objective(summary: dict) -> float:
    """A setting's score on the design sessions: its Sharpe, if it traded enough to have one."""
    if summary["trades"] < MIN_DESIGN_TRADES or summary.get("sharpe") is None:
        return FLOOR_SCORE + min(summary["trades"], MIN_DESIGN_TRADES) / 100
    return float(summary["sharpe"])


class Searcher:
    """Runs settings on design or validation sessions, once per distinct rule set."""

    def __init__(
        self,
        data_dir: Path,
        config_dir: Path,
        config: BacktestConfig,
        windows: LadderWindows,
        now: datetime,
        progress: Callable[[int, int], None] | None = None,
    ):
        self.data_dir, self.config_dir, self.config = data_dir, config_dir, config
        self.windows, self.now, self.progress = windows, now, progress
        self.sessions = History(data_dir).sessions
        self.registry = Registry(data_dir)
        self.digest = config_hash(config_dir)
        self.design: dict[str, dict] = {}
        self.validated: dict[str, dict] = {}
        self.runs = 0

    def _run(self, specs: list[StrategySpec], window: str) -> list[dict]:
        w = self.windows
        start, end = (
            (w.design_start, w.design_end)
            if window == "design"
            else (w.validate_start, w.validate_end)
        )
        cache = self.design if window == "design" else self.validated
        fresh = {signature(spec): spec for spec in specs if signature(spec) not in cache}
        if fresh:
            tasks = [
                (str(self.data_dir), str(self.config_dir), spec.model_dump_json(), start, end, 1.0)
                for spec in fresh.values()
            ]
            runs = run_tasks(tasks, self.config.lab.workers, self.sessions, self.progress)
            for (mark, spec), run in zip(fresh.items(), runs, strict=True):
                summary = _summary(run)
                self.runs += 1
                self.registry.record_trial(
                    self.now,
                    f"search_{window}",
                    self.digest,
                    start,
                    end,
                    {"strategy": spec.id, "signature": mark},
                    summary.get("sharpe"),
                    summary["net_pnl"],
                    summary["trades"],
                )
                cache[mark] = {key: summary.get(key) for key in TRIAL_KEYS}
        return [cache[signature(spec)] for spec in specs]

    def design_scores(self, specs: list[StrategySpec]) -> list[dict]:
        return self._run(specs, "design")

    def validate_scores(self, specs: list[StrategySpec]) -> list[dict]:
        return self._run(specs, "validate")


def run_search(
    spec: StrategySpec,
    params: list[Param],
    method: Method,
    max_trials: int,
    searcher: Searcher,
    note: Callable[[str], None] = lambda _: None,
    should_stop: Callable[[], bool] = lambda: False,
) -> dict:
    """Search the settings on design sessions; returns every trial, best first."""
    if not params:
        raise ValueError("Choose at least one setting to search.")
    if len(params) > MAX_PARAMS:
        raise ValueError(f"Search at most {MAX_PARAMS} settings at once.")
    study = optuna.create_study(direction="maximize", sampler=_sampler(method, max_trials))
    distributions = {param.path: _distribution(param) for param in params}
    batch_size = max(1, searcher.config.lab.workers)
    trials: list[dict] = []
    seen: dict[str, dict] = {}
    if method == "grid":
        combos = _grid(params, max_trials)
        total = len(combos)
        for combo in combos:
            study.enqueue_trial(combo)
    else:
        total = max_trials
        # The starting setting is trial 0, so the search reports how far it moved.
        study.enqueue_trial({param.path: param.clip(param.current) for param in params})
    while len(trials) < total and not should_stop():
        batch = [study.ask(distributions) for _ in range(min(batch_size, total - len(trials)))]
        candidates = []
        for trial in batch:
            values = {path: trial.params[path] for path in distributions}
            candidates.append((trial, values, apply_values(spec, values)))
        valid = [child for _, _, child in candidates if child is not None]
        scores = iter(searcher.design_scores(valid))
        for trial, values, child in candidates:
            if child is None:
                study.tell(trial, state=optuna.trial.TrialState.FAIL)
                trials.append({"number": trial.number, "values": values, "valid": False})
                continue
            design = next(scores)
            score = objective(design)
            study.tell(trial, score)
            mark = signature(child)
            row = {
                "number": trial.number,
                "values": values,
                "valid": True,
                "signature": mark,
                "design": design,
                "score": score,
            }
            trials.append(row)
            seen.setdefault(mark, row)
        note(f"Searched {len(trials)} of {total} settings ({method})")
    ranked = sorted((row for row in seen.values()), key=lambda row: row["score"], reverse=True)
    return {"trials": trials, "ranked": ranked, "planned": total}


def sensitivity(
    spec: StrategySpec,
    params: list[Param],
    center: dict[str, float],
    searcher: Searcher,
    min_share: float,
) -> dict:
    """Design-session Sharpe on a grid around the best setting, for its first two settings.

    The setting is robust when at least `min_share` of its direct neighbours keep half its
    Sharpe; a peak with nothing like it nearby is likely luck."""
    axes = params[:2]
    lines = []
    for param in axes:
        span = (param.high - param.low) / 8
        step = max(1, round(span)) if param.kind == "int" else max(param.step, span)
        middle = SENSITIVITY_POINTS // 2
        values = [
            param.clip(center[param.path] + (i - middle) * step) for i in range(SENSITIVITY_POINTS)
        ]
        lines.append(sorted(set(values)))
    xs = lines[0]
    ys = lines[1] if len(lines) > 1 else [None]
    grid: list[tuple[int, int, StrategySpec | None]] = []
    for j, y in enumerate(ys):
        for i, x in enumerate(xs):
            values = dict(center)
            values[axes[0].path] = x
            if y is not None:
                values[axes[1].path] = y
            grid.append((i, j, apply_values(spec, values)))
    valid = [child for _, _, child in grid if child is not None]
    scores = iter(searcher.design_scores(valid))
    cells = []
    for i, j, child in grid:
        design = next(scores) if child is not None else None
        cells.append(
            {
                "i": i,
                "j": j,
                "x": xs[i],
                "y": ys[j],
                "valid": child is not None,
                "sharpe": None if design is None else design.get("sharpe"),
                "trades": None if design is None else design["trades"],
                "net_pnl": None if design is None else design["net_pnl"],
                "score": None if design is None else objective(design),
            }
        )
    ci = xs.index(center[axes[0].path]) if center[axes[0].path] in xs else len(xs) // 2
    cy = center[axes[1].path] if len(axes) > 1 else None
    cj = ys.index(cy) if cy in ys else len(ys) // 2
    middle = next(c for c in cells if c["i"] == ci and c["j"] == cj)
    neighbours = [
        c
        for c in cells
        if (c["i"], c["j"]) != (ci, cj) and abs(c["i"] - ci) <= 1 and abs(c["j"] - cj) <= 1
    ]
    best = middle["score"]
    if best is None or best <= 0:
        share, robust = 0.0, False
    else:
        holding = [c for c in neighbours if c["score"] is not None and c["score"] >= best / 2]
        share = len(holding) / len(neighbours) if neighbours else 0.0
        robust = share >= min_share
    return {
        "x": asdict(axes[0]),
        "y": asdict(axes[1]) if len(axes) > 1 else None,
        "xs": xs,
        "ys": ys,
        "cells": cells,
        "center": {"i": ci, "j": cj},
        "neighbour_share": share,
        "robust": robust,
    }


def searched_child(root: StrategySpec, values: dict[str, float], params: list[Param]):
    """The searched setting as a strategy of its own, named after where it came from."""
    child = apply_values(root, values)
    if child is None:
        return None
    base = root.name.split(", searched")[0][:44]
    name = f"{base}, searched"[:60]
    draft = child.model_copy(
        update={
            "name": name,
            "source": "ladder",
            "parent_id": root.id,
            "step": f"search: {describe(values, params)}"[:120],
            "created_at": None,
        }
    )
    return draft.model_copy(update={"id": make_id(name, signature(draft))})

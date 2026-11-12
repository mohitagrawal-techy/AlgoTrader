"""Builds a strategy from simple to complex, keeping only the changes that earn their place.

Each rung changes one thing about the strategy below it. A change survives only if it is better
on sessions it was not designed on, and by more than luck explains. The final test period stays
unread until a single winner has been chosen.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from algoverse.backtest.config import BacktestConfig, LabConfig, config_hash
from algoverse.backtest.dataview import History
from algoverse.backtest.registry import Registry
from algoverse.backtest.walkforward import load_services
from algoverse.dashboard import clean
from algoverse.lab.evaluate import _summary, run_tasks, split_windows
from algoverse.lab.evidence import prob_better
from algoverse.lab.spec import (
    Condition,
    LegSpec,
    StrategySpec,
    complexity_level,
    describe_entry,
    describe_legs,
    make_id,
    signature,
)

FILTER = "filter"
STRUCTURE = "structure"
EXIT = "exit"
TIMING = "timing"
MIN_WINDOW_SESSIONS = 10
MAX_CONDITIONS = 6
TARGETS = (0.4, 0.8, 1.0)
STOPS = (0.35, 0.7)
DTE_WINDOWS = ((3, 7), (10, 25))
MIN_WING_DELTA = 0.05
STARTER_STOP = 0.4


def _root(id_: str, name: str, bias: str, legs: list[dict], rationale: str) -> StrategySpec:
    return StrategySpec(
        id=id_,
        name=name,
        rationale=rationale,
        source="ladder",
        bias=bias,
        legs=legs,
        entry={"dte_min": 5, "dte_max": 15},
        exits={"target_frac": 0.6, "stop_frac": STARTER_STOP, "exit_dte": 1},
        rung=1,
    )


def _leg(action: str, kind: str, delta: float, ratio: int = 1) -> dict:
    return {
        "action": action,
        "option_type": kind,
        "select": "delta",
        "value": delta,
        "ratio": ratio,
    }


# Out of the money with a tight stop. On 10 Nov 2026 quotes this risked about Rs 3,200 on one
# NIFTY lot. An at-the-money option or a 50% stop can exceed a 1% limit on a Rs 5 lakh account.
ROOTS = [
    _root(
        "start_otm_call",
        "Long OTM call",
        "bullish",
        [_leg("buy", "CE", 0.25)],
        "Pays a small premium for upside.",
    ),
    _root(
        "start_otm_put",
        "Long OTM put",
        "bearish",
        [_leg("buy", "PE", 0.25)],
        "Pays a small premium for downside.",
    ),
    _root(
        "start_long_strangle",
        "Long strangle",
        "neutral",
        [_leg("buy", "CE", 0.15), _leg("buy", "PE", 0.15)],
        "Pays for a large move in either direction.",
    ),
]


@dataclass(frozen=True)
class LadderWindows:
    design_start: date
    design_end: date
    validate_start: date
    validate_end: date
    final_start: date
    final_end: date
    design_sessions: int
    validate_sessions: int
    final_sessions: int


def ladder_windows(sessions: list[date], config: BacktestConfig) -> LadderWindows | None:
    """Splits the lab's training period in two. The lab's test period stays the final test."""
    base = split_windows(sessions, config)
    if base is None:
        return None
    earlier = [day for day in sessions if base.train_start <= day <= base.train_end]
    cut = int(len(earlier) * (1 - config.lab.validate_share))
    if cut < MIN_WINDOW_SESSIONS or len(earlier) - cut < MIN_WINDOW_SESSIONS:
        return None
    return LadderWindows(
        earlier[0],
        earlier[cut - 1],
        earlier[cut],
        earlier[-1],
        base.test_start,
        base.test_end,
        cut,
        len(earlier) - cut,
        base.test_sessions,
    )


def _is_debit(spec: StrategySpec) -> bool:
    return spec.legs[0].action == "buy"


def _filter_steps(spec: StrategySpec) -> list[tuple[str, dict]]:
    """Single conditions worth trying, chosen by what the strategy needs to go right."""
    if spec.bias == "bullish":
        steps = [
            ("only in an uptrend", {"trend": ["up"]}),
            ("only when OI build-up is bullish", ("oi_buildup", "above", 0.0)),
            ("only when India VIX is falling", ("vix_change_pct", "below", 0.0)),
            ("only when put-call ratio is above 1", ("pcr", "above", 1.0)),
        ]
    elif spec.bias == "bearish":
        steps = [
            ("only in a downtrend", {"trend": ["down"]}),
            ("only when OI build-up is bearish", ("oi_buildup", "below", 0.0)),
            ("only when India VIX is rising", ("vix_change_pct", "above", 0.0)),
            ("only when put-call ratio is below 0.8", ("pcr", "below", 0.8)),
        ]
    else:
        steps = [
            ("only in a range-bound market", {"trend": ["range"]}),
            ("only when ADX is below 20", ("adx", "below", 20.0)),
        ]
    if _is_debit(spec):
        steps += [
            ("only when options are cheap vs realised volatility", ("iv_minus_rv", "below", 0.0)),
            ("only when IV rank is below 50", {"iv_rank_max": 50.0}),
        ]
    else:
        steps += [
            ("only when options are rich vs realised volatility", ("iv_minus_rv", "above", 0.0)),
            ("only when IV rank is above 50", {"iv_rank_min": 50.0}),
        ]
    if spec.bias != "neutral":
        steps.append(("only when the trend is strong (ADX above 25)", ("adx", "above", 25.0)))
    built = []
    for label, change in steps:
        if isinstance(change, tuple):
            signal, op, value = change
            if any(c.signal == signal for c in spec.entry.conditions):
                continue
            if len(spec.entry.conditions) >= MAX_CONDITIONS:
                continue
            condition = Condition(signal=signal, op=op, value=value)
            built.append((label, {"conditions": [*spec.entry.conditions, condition]}))
        elif "trend" in change and not spec.entry.trend:
            built.append((label, change))
        elif "iv_rank_max" in change and spec.entry.iv_rank_max is None:
            built.append((label, change))
        elif "iv_rank_min" in change and spec.entry.iv_rank_min is None:
            built.append((label, change))
    return built


def _wing(delta: float) -> float:
    """A hedge sold or bought at half the delta of the leg beside it."""
    return round(delta / 2, 2)


def _structure_steps(spec: StrategySpec) -> list[tuple[str, list[LegSpec]]]:
    legs = spec.legs
    if not all(leg.select == "delta" and leg.expiry == 0 for leg in legs):
        return []
    first = legs[0]
    if len(legs) == 1 and first.action == "buy" and _wing(first.value) >= MIN_WING_DELTA:
        wing = LegSpec(
            action="sell", option_type=first.option_type, select="delta", value=_wing(first.value)
        )
        return [("turn into a debit spread", [first, wing])]
    is_vertical = (
        len(legs) == 2
        and legs[0].option_type == legs[1].option_type
        and {legs[0].action, legs[1].action} == {"buy", "sell"}
    )
    if is_vertical and legs[1].action == "sell" and _wing(legs[1].value) >= MIN_WING_DELTA:
        doubled = legs[1].model_copy(update={"ratio": 2})
        far = LegSpec(
            action="buy", option_type=first.option_type, select="delta", value=_wing(legs[1].value)
        )
        return [("turn into a butterfly", [first, doubled, far])]
    is_straddle = len(legs) == 2 and all(leg.action == "buy" for leg in legs)
    if (
        is_straddle
        and legs[0].option_type != legs[1].option_type
        and all(_wing(leg.value) >= MIN_WING_DELTA for leg in legs)
    ):
        wings = [
            LegSpec(
                action="sell", option_type=leg.option_type, select="delta", value=_wing(leg.value)
            )
            for leg in legs
        ]
        return [("sell far wings to cut the cost", [*legs, *wings])]
    return []


def _make_child(parent: StrategySpec, step: str, update: dict) -> StrategySpec | None:
    data = parent.model_dump()
    data.update(update.get("spec", {}))
    data["entry"] = {**data["entry"], **update.get("entry", {})}
    data["exits"] = {**data["exits"], **update.get("exits", {})}
    root_name = parent.name.split(", rung")[0]
    rung = parent.rung + 1
    try:
        draft = StrategySpec.model_validate(
            {
                **data,
                "id": "draft",
                "name": f"{root_name}, rung {rung}"[:60],
                "source": "ladder",
                "parent_id": parent.id,
                "rung": rung,
                "step": step,
            }
        )
    except ValueError:
        return None
    return draft.model_copy(update={"id": make_id(draft.name, signature(draft))})


def propose_steps(
    parent: StrategySpec, limit: int, seen: set[str] | None = None
) -> list[tuple[str, str, StrategySpec]]:
    """Children of a strategy, each differing by one idea: (step, kind, child)."""
    seen = set() if seen is None else seen
    ideas: list[tuple[str, str, dict]] = []
    for label, change in _filter_steps(parent):
        ideas.append((label, FILTER, {"entry": change}))
    for label, legs in _structure_steps(parent):
        ideas.append((label, STRUCTURE, {"spec": {"legs": [leg.model_dump() for leg in legs]}}))
    for target in TARGETS:
        if target != parent.exits.target_frac:
            ideas.append(
                (f"take profit at {target:g}x premium", EXIT, {"exits": {"target_frac": target}})
            )
    for stop in STOPS:
        if stop != parent.exits.stop_frac:
            ideas.append((f"cut loss at {stop:g}x premium", EXIT, {"exits": {"stop_frac": stop}}))
    for low, high in DTE_WINDOWS:
        if (low, high) != (parent.entry.dte_min, parent.entry.dte_max):
            ideas.append(
                (
                    f"enter {low}-{high} days before expiry",
                    TIMING,
                    {"entry": {"dte_min": low, "dte_max": high}},
                )
            )
    # A mix of kinds, so a small limit does not spend every try on filters.
    by_kind: dict[str, list] = {FILTER: [], STRUCTURE: [], EXIT: [], TIMING: []}
    for idea in ideas:
        by_kind[idea[1]].append(idea)
    mixed = []
    while any(by_kind.values()):
        for group in by_kind.values():
            if group:
                mixed.append(group.pop(0))
    steps = []
    for label, kind, update in mixed:
        child = _make_child(parent, label, update)
        if child is None:
            continue
        mark = signature(child)
        if mark in seen:
            continue
        seen.add(mark)
        steps.append((label, kind, child))
        if len(steps) == limit:
            break
    return steps


def _sharpe(summary: dict) -> float:
    value = summary.get("sharpe")
    return float("-inf") if value is None else value


def _too_few(summary: dict) -> str:
    blocked = summary.get("blocked") or {}
    text = "too few trades on unseen sessions"
    return f"{text}, mostly: {next(iter(blocked))}" if blocked else text


def judge_step(child: dict, parent: dict, tries: int, config: LabConfig) -> tuple[bool, str, float]:
    """Whether a change earned its place. Tries is how many changes were tested for this rung."""
    validate, base = child["validate"], parent["validate"]
    probability = prob_better(child["validate_daily"], parent["validate_daily"])
    if validate["trades"] < config.ladder_min_trades:
        return False, _too_few(validate), probability
    if validate["net_pnl"] <= 0:
        return False, "loses money on unseen sessions", probability
    if _sharpe(validate) <= _sharpe(base):
        return False, "no better than the simpler version on unseen sessions", probability
    design, design_base = child["design"], parent["design"]
    if (design.get("expectancy") or float("-inf")) < (
        design_base.get("expectancy") or float("-inf")
    ):
        return False, "only looks better on unseen sessions, not on design sessions", probability
    needed = 1 - config.ladder_alpha / max(tries, 1)
    if probability < needed:
        return (
            False,
            f"improvement could be luck ({probability:.0%}, needs {needed:.0%})",
            probability,
        )
    return True, "better on unseen sessions, by more than luck explains", probability


def _public(node: dict, accepted: bool | None, reason: str | None) -> dict:
    """What a report shows. It holds nothing from the final test period."""
    spec: StrategySpec = node["spec"]
    return {
        "id": spec.id,
        "name": spec.name,
        "parent_id": spec.parent_id,
        "rung": spec.rung,
        "step": spec.step,
        "kind": node.get("kind"),
        "level": complexity_level(spec),
        "legs": describe_legs(spec),
        "entry": describe_entry(spec),
        "design": node["design"],
        "validate": node["validate"],
        "prob_better": node.get("prob_better"),
        "accepted": accepted,
        "reason": reason,
    }


def run_ladder(
    data_dir: Path,
    config_dir: Path,
    roots: list[StrategySpec],
    now: datetime,
    note: Callable[[str], None] = lambda _: None,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Climb from each root, then name one winner. Returns the report and the winner's spec."""
    config = load_services(config_dir).backtest
    lab = config.lab
    sessions = History(data_dir).sessions
    windows = ladder_windows(sessions, config)
    if windows is None:
        raise ValueError("There is not enough history to split into design and unseen sessions.")
    registry = Registry(data_dir)
    digest = config_hash(config_dir)
    pool = ProcessPoolExecutor(max_workers=lab.workers) if lab.workers > 1 else None
    trials = 0

    def score(specs: list[StrategySpec]) -> dict[str, dict]:
        nonlocal trials
        tasks = []
        for spec in specs:
            base = (str(data_dir), str(config_dir), spec.model_dump_json())
            tasks += [
                (*base, windows.design_start, windows.design_end, 1.0),
                (*base, windows.validate_start, windows.validate_end, 1.0),
            ]
        runs = iter(run_tasks(tasks, lab.workers, sessions, progress, pool))
        scored = {}
        for spec in specs:
            design_run, validate_run = next(runs), next(runs)
            design, validate = _summary(design_run), _summary(validate_run)
            trials += 1
            registry.record_trial(
                now,
                "ladder_validate",
                digest,
                windows.validate_start,
                windows.validate_end,
                {"strategy": spec.id, "signature": signature(spec), "step": spec.step},
                validate.get("sharpe"),
                validate["net_pnl"],
                validate["trades"],
            )
            scored[spec.id] = {
                "spec": spec,
                "design": design,
                "validate": validate,
                "validate_daily": validate_run["daily"],
            }
        return scored

    try:
        note(f"Rung 1: testing {len(roots)} simple starting strategies")
        scored_roots = score(roots)
        ranked = sorted(scored_roots.values(), key=lambda n: _sharpe(n["validate"]), reverse=True)
        usable = [n for n in ranked if n["validate"]["trades"] >= lab.ladder_min_trades]
        rungs = [
            {
                "rung": 1,
                "tried": [
                    _public(
                        node,
                        node in usable[: lab.ladder_beam],
                        "kept as a starting point"
                        if node in usable[: lab.ladder_beam]
                        else _too_few(node["validate"])
                        if node not in usable
                        else "other starting points did better on unseen sessions",
                    )
                    for node in ranked
                ],
            }
        ]
        beam = usable[: lab.ladder_beam]
        path: dict[str, dict] = {node["spec"].id: node for node in beam}
        seen = {signature(node["spec"]) for node in ranked}
        for number in range(2, lab.ladder_rungs + 1):
            if not beam:
                break
            steps = [
                (parent, label, kind, child)
                for parent in beam
                for label, kind, child in propose_steps(parent["spec"], lab.ladder_steps, seen)
            ]
            if not steps:
                break
            note(f"Rung {number}: testing {len(steps)} one-step changes")
            scored = score([child for _, _, _, child in steps])
            tried, survivors = [], []
            for parent, _, kind, child in steps:
                node = scored[child.id] | {"kind": kind}
                accepted, reason, probability = judge_step(node, parent, len(steps), lab)
                node["prob_better"] = probability
                tried.append(_public(node, accepted, reason))
                if accepted:
                    survivors.append(node)
                    path[child.id] = node
            rungs.append({"rung": number, "tried": tried})
            survivors.sort(key=lambda n: _sharpe(n["validate"]), reverse=True)
            beam = survivors[: lab.ladder_beam]
            if not beam:
                note(f"Rung {number}: no change earned its place, so the climb stops here")
    finally:
        if pool:
            pool.shutdown()
    winner = max(
        path.values(),
        key=lambda n: (n["spec"].rung, _sharpe(n["validate"])),
        default=None,
    )
    lineage = []
    node = winner
    while node is not None:
        lineage.insert(0, _public(node, True, None))
        node = path.get(node["spec"].parent_id) if node["spec"].parent_id else None
    report = {
        "run_at": now,
        "windows": windows.__dict__,
        "trials": trials,
        "rungs": rungs,
        "winner_id": winner["spec"].id if winner else None,
        "lineage": lineage,
    }
    return {"report": clean(report), "winner": winner["spec"] if winner else None, "trials": trials}

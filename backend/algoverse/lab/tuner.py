"""Tunes one strategy a setting at a time, as a tree of tested changes.

Each round, the AI model (or a built-in list when it is off) reads the current best version's
results on the design sessions and suggests changes to single settings. Every change is
backtested on design sessions and on later unseen sessions. A change is kept only if it is
better on the unseen sessions by more than luck explains, the same test the ladder uses. The
best kept change becomes the parent for the next round.

The AI never sees unseen-session results, only whether each change was kept, so its
suggestions cannot be fitted to the sessions that judge them. The lab's final test period is
read only once, for the one version chosen at the end.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from algoverse.ai.budget import AiLog
from algoverse.ai.client import LLMClient, LLMError
from algoverse.ai.schemas import OK, UNAVAILABLE
from algoverse.backtest.config import BacktestConfig, config_hash
from algoverse.backtest.registry import Registry
from algoverse.backtest.walkforward import load_services
from algoverse.dashboard import clean
from algoverse.lab.evaluate import _run, _summary
from algoverse.lab.ladder import LadderWindows, judge_step
from algoverse.lab.spec import (
    StrategySpec,
    complexity_level,
    describe_entry,
    describe_legs,
    make_id,
    signature,
)

log = logging.getLogger(__name__)
MAX_GUIDANCE = 500
MAX_HISTORY = 30
# Lists a change may add one item to, with a path ending in ".+".
GROWABLE = {"legs", "entry.conditions", "entry.rules", "entry.any_rules", "exits.rules"}
TOP_LEVEL = {"legs", "entry", "exits", "decide_every"}
# Rounds in a row with nothing kept before the tuner stops.
PATIENCE = 2
SUMMARY_KEYS = ("trades", "net_pnl", "win_rate", "expectancy", "sharpe", "max_drawdown")


@dataclass(frozen=True)
class Change:
    path: str
    value: Any
    reason: str = ""


@dataclass
class Suggestions:
    analysis: str
    changes: list[Change]
    source: str  # "ai" or "built_in"
    notes: list[str] = field(default_factory=list)


# ---- Applying one change ------------------------------------------------------------------


def _tokens(path: str) -> list[str | int]:
    return [int(part) if part.isdigit() else part for part in path.split(".")]


def _show(value: Any) -> str:
    if value is None:
        return "off"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))[:80]
    return str(value)


def apply_change(spec: StrategySpec, change: Change) -> tuple[StrategySpec | None, str]:
    """The strategy with one setting changed, and a label. None and the reason when invalid.

    Paths are dotted, with list positions as numbers: "exits.stop_frac", "legs.0.value",
    "entry.rules.1.right.params.period". A path ending in ".+" adds one item to a list, and a
    null value at a list position removes that item.
    """
    tokens = _tokens(change.path.strip())
    if not tokens or tokens[0] not in TOP_LEVEL:
        return None, f"{change.path}: only legs, entry, exits and decide_every can change"
    data = spec.model_dump(mode="json")
    holder: Any = data
    for token in tokens[:-1]:
        try:
            holder = holder[token]
        except (KeyError, IndexError, TypeError):
            return None, f"{change.path}: no such setting"
    last = tokens[-1]
    if last == "+":
        parent_path = ".".join(str(t) for t in tokens[:-1])
        if parent_path not in GROWABLE or not isinstance(holder, list):
            return None, f"{change.path}: items can be added only to {', '.join(sorted(GROWABLE))}"
        holder.append(change.value)
        label = f"add to {parent_path}: {_show(change.value)}"
    elif isinstance(holder, list) and isinstance(last, int):
        if not 0 <= last < len(holder):
            return None, f"{change.path}: no such setting"
        if change.value is None:
            holder.pop(last)
            label = f"remove {change.path}"
        else:
            old = holder[last]
            holder[last] = change.value
            label = f"{change.path}: {_show(old)} → {_show(change.value)}"
    elif isinstance(holder, dict) and last in holder:
        old = holder[last]
        if old == change.value:
            return None, f"{change.path}: already {_show(old)}"
        holder[last] = change.value
        label = f"{change.path}: {_show(old)} → {_show(change.value)}"
    elif isinstance(holder, dict) and tokens[-2:-1] == ["params"]:
        # An indicator setting left at its default is not stored, so it may be set fresh.
        holder[last] = change.value
        label = f"{change.path}: default → {_show(change.value)}"
    else:
        return None, f"{change.path}: no such setting"
    try:
        child = StrategySpec.model_validate(data)
    except ValidationError as exc:
        return None, f"{change.path}: {exc.errors()[0]['msg']}"
    if signature(child) == signature(spec):
        return None, f"{change.path}: changes nothing that is traded"
    return child, label


def make_child(parent: StrategySpec, root_name: str, depth: int, label: str, child: StrategySpec):
    name = f"{root_name}, tuned {depth}"[:60]
    draft = child.model_copy(
        update={
            "name": name,
            "source": "ladder",
            "parent_id": parent.id,
            "rung": min(depth + 1, 20),
            "step": label[:120],
            "created_at": None,
        }
    )
    return draft.model_copy(update={"id": make_id(name, signature(draft))})


# ---- Built-in suggestions -----------------------------------------------------------------


def _round_like(value: float, old: float) -> float:
    return round(value) if float(old).is_integer() and abs(old) >= 2 else round(value, 2)


def built_in_changes(spec: StrategySpec) -> list[Change]:
    """Nudges to the numbers already in the strategy, used when the AI model is off."""
    exits, entry = spec.exits, spec.entry
    changes = [
        Change(
            "exits.target_frac",
            _round_like(exits.target_frac * 1.5, exits.target_frac),
            "let winners run further",
        ),
        Change(
            "exits.stop_frac",
            _round_like(exits.stop_frac * 0.7, exits.stop_frac),
            "cut losers sooner",
        ),
        Change(
            "exits.target_frac",
            _round_like(exits.target_frac * 0.6, exits.target_frac),
            "bank profits sooner",
        ),
        Change(
            "exits.stop_frac",
            _round_like(exits.stop_frac * 1.4, exits.stop_frac),
            "give trades more room",
        ),
    ]
    for index, leg in enumerate(spec.legs):
        if leg.select == "delta":
            for step in (0.05, -0.05):
                value = round(leg.value + step, 2)
                if 0.05 <= value <= 0.95:
                    changes.append(Change(f"legs.{index}.value", value, "move this strike"))
        else:
            for step in (1, -1):
                changes.append(Change(f"legs.{index}.value", leg.value + step, "move this strike"))
    if entry.dte_max - entry.dte_min > 4:
        changes.append(
            Change(
                "entry.dte_max",
                entry.dte_min + (entry.dte_max - entry.dte_min) // 2,
                "enter closer to expiry",
            )
        )
    if exits.exit_dte > 0:
        changes.append(Change("exits.exit_dte", exits.exit_dte - 1, "hold a day longer"))
    for section, rules in (("entry.rules", entry.rules), ("exits.rules", exits.rules)):
        for index, rule in enumerate(rules):
            if rule.type != "compare":
                continue
            for side in ("left", "right"):
                operand = getattr(rule, side)
                if operand.kind == "value" and operand.value:
                    changes.append(
                        Change(
                            f"{section}.{index}.{side}.value",
                            _round_like(operand.value * 1.1, operand.value),
                            "loosen or tighten this level",
                        )
                    )
                for name, value in operand.params.items():
                    changes.append(
                        Change(
                            f"{section}.{index}.{side}.params.{name}",
                            _round_like(value * 1.5, value),
                            "slower indicator",
                        )
                    )
    for index, condition in enumerate(entry.conditions):
        if condition.value:
            changes.append(
                Change(
                    f"entry.conditions.{index}.value",
                    _round_like(condition.value * 1.2, condition.value),
                    "move this threshold",
                )
            )
    if exits.trail is None:
        changes.append(Change("exits.trail", exits.stop_frac, "trail the stop behind gains"))
    return changes


# ---- AI suggestions -----------------------------------------------------------------------

SYSTEM = """You tune a rule-based option strategy for Indian index options. You are shown the \
strategy as JSON, its backtest results on DESIGN sessions, and every change already tried with \
whether it was kept. A change is kept only when it does better on later unseen sessions, which \
you never see. You are not giving investment advice.

Reply with:
- analysis: two to four sentences on what the results say is wrong or working (for example: \
too few trades, stops hit too often, winners closed too early, blocked by the loss limit).
- suggestions: changes to try, best first. Each changes ONE setting and has:
  path: dotted path into the strategy JSON, with list positions as numbers. Examples: \
"exits.stop_frac", "exits.target_unit", "legs.0.value", "entry.dte_max", "entry.trend", \
"entry.rules.0.right.params.period", "exits.trail". End a path with ".+" to add one item to \
legs, entry.conditions, entry.rules, entry.any_rules or exits.rules; the value is then the whole \
new item. A null value at a list position removes that item.
  value: the new value, in the same shape as the current one.
  reason: one sentence tying the change to the analysis.

Rules: one setting per suggestion; never repeat a change already tried; small samples are \
noise, so prefer changes with a clear reason over chasing one result; keep the strategy's loss \
at the stop within the account's loss limit. Exit amounts are in the unit beside them: premium \
(a fraction of the premium paid or received), rupees per lot, or option points."""


class AiSuggestion(BaseModel):
    path: str
    value: Any = None
    reason: str = ""


class AiTuning(BaseModel):
    analysis: str
    suggestions: list[AiSuggestion]


def ai_changes(
    client: LLMClient,
    ai_log: AiLog,
    now: datetime,
    spec: StrategySpec,
    feedback: dict,
    tried: list[dict],
    count: int,
    account: dict,
    guidance: str,
    rules_help: str,
) -> Suggestions:
    user = json.dumps(
        {
            "suggest": count,
            "account": account,
            "owner_guidance": guidance[:MAX_GUIDANCE] or None,
            "strategy": spec.model_dump(
                mode="json", include={"bias", "legs", "decide_every", "entry", "exits"}
            ),
            "design_results": feedback,
            "already_tried": tried[-MAX_HISTORY:],
        },
        default=str,
    )
    system = SYSTEM + rules_help
    started = time.monotonic()
    text, status, tokens = "", UNAVAILABLE, (0, 0)
    try:
        reply = client.complete(system, user, AiTuning.model_json_schema())
        text, tokens = reply.text, (reply.input_tokens, reply.output_tokens)
        parsed = AiTuning.model_validate_json(text)
        status = OK
    except LLMError as exc:
        text = str(exc)
        raise
    except ValidationError as exc:
        raise LLMError("The AI model returned suggestions in an unreadable format.") from exc
    finally:
        ai_log.record(
            ts=now.isoformat(),
            day=now.date().isoformat(),
            kind="strategy_tuning",
            key=spec.id,
            model=client.model,
            status=status,
            prompt=f"{system}\n\n{user}",
            output=text,
            payload=None,
            input_tokens=tokens[0],
            output_tokens=tokens[1],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    changes = [Change(s.path, s.value, s.reason[:300]) for s in parsed.suggestions[: count * 2]]
    return Suggestions(parsed.analysis[:1200], changes, "ai")


# ---- Scoring and the tree -----------------------------------------------------------------


def design_feedback(node: dict) -> dict:
    """What the AI may read about a version: design-session results only."""
    return {
        **{key: node["design"].get(key) for key in SUMMARY_KEYS},
        "why_days_were_skipped": node["design"].get("blocked") or {},
        "exit_reasons": node.get("design_exits") or {},
        "average_holding_days": node["design"].get("average_holding_days"),
    }


def _exit_reasons(trades: list[dict]) -> dict[str, int]:
    counts = Counter(
        (trade.get("exit_reason") or "unknown").split(":")[0].split(" for ")[0] for trade in trades
    )
    return dict(counts.most_common(6))


class Scorer:
    """Backtests versions on the design and unseen sessions, in parallel when configured."""

    def __init__(self, data_dir: Path, config_dir: Path, windows: LadderWindows, now: datetime):
        self.data_dir, self.config_dir, self.windows, self.now = data_dir, config_dir, windows, now
        self.config: BacktestConfig = load_services(config_dir).backtest
        self.registry = Registry(data_dir)
        self.digest = config_hash(config_dir)
        self.trials = 0

    def score(self, specs: list[StrategySpec]) -> dict[str, dict]:
        w = self.windows
        tasks = []
        for spec in specs:
            base = (str(self.data_dir), str(self.config_dir), spec.model_dump_json())
            tasks += [
                (*base, w.design_start, w.design_end, 1.0),
                (*base, w.validate_start, w.validate_end, 1.0),
            ]
        workers = min(self.config.lab.workers, len(tasks))
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                runs = iter(list(pool.map(_run, tasks)))
        else:
            runs = iter([_run(task) for task in tasks])
        scored = {}
        for spec in specs:
            design_run, validate_run = next(runs), next(runs)
            validate = _summary(validate_run)
            self.trials += 1
            self.registry.record_trial(
                self.now,
                "tune_validate",
                self.digest,
                w.validate_start,
                w.validate_end,
                {"strategy": spec.id, "signature": signature(spec), "step": spec.step},
                validate.get("sharpe"),
                validate["net_pnl"],
                validate["trades"],
            )
            scored[spec.id] = {
                "design": _summary(design_run),
                "design_exits": _exit_reasons(design_run["trades"]),
                "validate": validate,
                "validate_daily": validate_run["daily"],
            }
        return scored


def new_node(spec: StrategySpec, scored: dict, depth: int, change: Change | None) -> dict:
    return {
        "id": spec.id,
        "parent_id": spec.parent_id if change else None,
        "depth": depth,
        "spec": spec.model_dump(mode="json"),
        "label": spec.step if change else spec.name,
        "path": change.path if change else None,
        "reason": change.reason if change else None,
        "level": complexity_level(spec),
        "legs_text": describe_legs(spec),
        "entry_text": describe_entry(spec),
        **scored,
        "accepted": None,
        "verdict": None,
        "prob_better": None,
    }


def tried_history(session: dict) -> list[dict]:
    return [
        {
            "change": node["label"],
            "kept": node["accepted"],
            "design_trades": node["design"].get("trades"),
            "design_net_pnl": node["design"].get("net_pnl"),
        }
        for node in session["nodes"].values()
        if node["depth"] > 0
    ]


def grow(
    session: dict,
    scorer: Scorer,
    from_id: str,
    rounds: int,
    per_round: int,
    suggest: Callable[[StrategySpec, dict, list[dict], int], Suggestions],
    note: Callable[[str], None] = lambda _: None,
) -> dict:
    """Run rounds of suggest, test and judge from one node. Changes `session` in place."""
    lab = scorer.config.lab
    nodes = session["nodes"]
    parent = nodes[from_id]
    seen = {signature(StrategySpec.model_validate(n["spec"])) for n in nodes.values()}
    misses = 0
    for _ in range(rounds):
        number = len(session["rounds"]) + 1
        parent_spec = StrategySpec.model_validate(parent["spec"])
        note(f"Round {number}: asking for changes to {parent['label']}")
        suggestions = suggest(
            parent_spec, design_feedback(parent), tried_history(session), per_round
        )
        record = {
            "round": number,
            "parent_id": parent["id"],
            "analysis": suggestions.analysis,
            "source": suggestions.source,
            "skipped": list(suggestions.notes),
            "tried": [],
            "kept": [],
        }
        session["rounds"].append(record)
        children = []
        for change in suggestions.changes:
            changed, label = apply_change(parent_spec, change)
            if changed is None:
                record["skipped"].append(label)
                continue
            child = make_child(
                parent_spec, session["root_name"], parent["depth"] + 1, label, changed
            )
            mark = signature(child)
            if mark in seen:
                record["skipped"].append(f"{label}: already tried")
                continue
            seen.add(mark)
            children.append((change, child))
            if len(children) == per_round:
                break
        if not children:
            note(f"Round {number}: no new valid change to test, so tuning stops here")
            break
        note(f"Round {number}: testing {len(children)} one-setting changes")
        scored = scorer.score([child for _, child in children])
        kept = []
        for change, child in children:
            node = new_node(child, scored[child.id], parent["depth"] + 1, change)
            accepted, verdict, probability = judge_step(node, parent, len(children), lab)
            node.update(accepted=accepted, verdict=verdict, prob_better=probability)
            nodes[child.id] = node
            record["tried"].append(child.id)
            if accepted:
                kept.append(node)
        kept.sort(key=lambda n: n["validate"].get("sharpe") or float("-inf"), reverse=True)
        record["kept"] = [n["id"] for n in kept]
        if kept:
            parent, misses = kept[0], 0
            session["best_id"] = parent["id"]
        else:
            misses += 1
            note(f"Round {number}: no change was kept")
            if misses >= PATIENCE:
                break
    session["trials"] = session.get("trials", 0) + scorer.trials
    scorer.trials = 0
    return session


def public(session: dict) -> dict:
    """The session for the page, without the per-day series the judge used."""
    nodes = {
        key: {k: v for k, v in node.items() if k != "validate_daily"}
        for key, node in session["nodes"].items()
    }
    return clean({**session, "nodes": nodes})

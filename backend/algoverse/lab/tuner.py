"""Tunes one strategy a setting at a time, as a tree of tested changes.

Each round, the AI model (or a built-in list when it is off) reads the current best version's
results on the design sessions and suggests changes to single settings. Every change is
backtested on design sessions and on later unseen sessions. A change is kept only if it is
better on the unseen sessions by more than luck explains, the same test the ladder uses. The
best kept change becomes the parent for the next round; when none is kept, the next round
tries other changes on the same version.

Every round tests exactly the number of changes asked for. When the AI model's suggestions
fall short (invalid, or tried before) it is asked again, and built-in nudges fill the rest.
A round has fewer only when no untried change is left.

The AI never sees unseen-session results, only whether each change was kept, so its
suggestions cannot be fitted to the sessions that judge them. The lab's final test period is
read only once, for the one version chosen at the end.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from algoverse.ai.budget import AiLog
from algoverse.ai.client import LLMClient, LLMError
from algoverse.ai.schemas import OK, UNAVAILABLE
from algoverse.backtest.config import BacktestConfig, config_hash
from algoverse.backtest.dataview import History
from algoverse.backtest.registry import Registry
from algoverse.backtest.walkforward import load_services
from algoverse.dashboard import clean
from algoverse.lab.evaluate import _summary, run_tasks
from algoverse.lab.ladder import LadderWindows, judge_step
from algoverse.lab.spec import (
    StrategySpec,
    complexity_level,
    describe_entry,
    describe_legs,
    make_id,
    signature,
)
from algoverse.lab.tune_params import allows, sweep_changes, tunable_params

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
MAX_GUIDANCE = 500
MAX_HISTORY = 30
# Lists a change may add one item to, with a path ending in ".+".
GROWABLE = {"legs", "entry.conditions", "entry.rules", "entry.any_rules", "exits.rules"}
TOP_LEVEL = {"legs", "entry", "exits", "decide_every"}
# Indicator period scales for filling a round, with the reason shown for each.
SPEEDS = (
    (0.67, "faster indicator"),
    (2.0, "much slower indicator"),
    (0.5, "much faster indicator"),
)
# Extra times the AI model is asked when its suggestions leave a round short.
EXTRA_ASKS = 2
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


def more_changes(spec: StrategySpec) -> list[Change]:
    """Wider and finer nudges than `built_in_changes`, to fill a round the first list cannot."""
    exits, entry = spec.exits, spec.entry
    changes = []
    for scale in (1.25, 0.8, 2.0, 0.5):
        changes.append(
            Change(
                "exits.target_frac",
                _round_like(exits.target_frac * scale, exits.target_frac),
                "move the profit target",
            )
        )
        changes.append(
            Change(
                "exits.stop_frac",
                _round_like(exits.stop_frac * scale, exits.stop_frac),
                "move the stop loss",
            )
        )
    if exits.trail is not None:
        for scale in (0.5, 1.5):
            changes.append(
                Change(
                    "exits.trail",
                    _round_like(exits.trail * scale, exits.trail),
                    "trail closer or looser",
                )
            )
    for index, leg in enumerate(spec.legs):
        if leg.select == "delta":
            steps = [round(leg.value + step, 2) for step in (0.1, -0.1, 0.15, -0.15)]
            values = [value for value in steps if 0.05 <= value <= 0.95]
        elif leg.select == "premium":
            values = [_round_like(leg.value * scale, leg.value) for scale in (1.25, 0.8, 1.5, 0.6)]
        else:
            values = [leg.value + step for step in (2, -2, 3, -3)]
        changes += [Change(f"legs.{index}.value", value, "move this strike") for value in values]
    for section, rules in (("entry.rules", entry.rules), ("exits.rules", exits.rules)):
        for index, rule in enumerate(rules):
            if rule.type != "compare":
                continue
            for side in ("left", "right"):
                operand = getattr(rule, side)
                if operand.kind == "value" and operand.value:
                    for scale in (0.9, 1.05, 0.95, 1.2):
                        changes.append(
                            Change(
                                f"{section}.{index}.{side}.value",
                                _round_like(operand.value * scale, operand.value),
                                "loosen or tighten this level",
                            )
                        )
                for name, value in operand.params.items():
                    for scale, why in SPEEDS:
                        changes.append(
                            Change(
                                f"{section}.{index}.{side}.params.{name}",
                                _round_like(value * scale, value),
                                why,
                            )
                        )
    for index, condition in enumerate(entry.conditions):
        if condition.value:
            for scale in (0.8, 1.1, 0.9, 1.4):
                changes.append(
                    Change(
                        f"entry.conditions.{index}.value",
                        _round_like(condition.value * scale, condition.value),
                        "move this threshold",
                    )
                )
    if entry.max_trades_per_day:
        for value in (entry.max_trades_per_day - 1, entry.max_trades_per_day + 1):
            if value >= 1:
                changes.append(
                    Change("entry.max_trades_per_day", value, "trade more or less often")
                )
    if exits.exit_dte > 1:
        changes.append(Change("exits.exit_dte", exits.exit_dte - 2, "hold two days longer"))
    changes.append(Change("exits.exit_dte", exits.exit_dte + 1, "close a day sooner"))
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

Allowed values: entry.trend is a list of "up", "down" and "range" (empty means any trend); \
decide_every is "day", "5m", "15m" or "1h"; units are "premium", "rupees" or "points"; times \
are "HH:MM" between 09:15 and 15:25. Indicator names and their settings are listed below.

When only_change is given, the owner chose which settings may change: suggest changes only to \
those paths, or to a part inside one (a chosen rule may be changed in any part or removed). A \
path ending in ".+" there allows adding one new item to that list.

Rules: one setting per suggestion; never repeat a change already tried; small samples are \
noise, so prefer changes with a clear reason over chasing one result; keep the strategy's loss \
at the stop within the account's loss limit. Exit amounts are in the unit beside them: premium \
(a fraction of the premium paid or received), rupees per lot, or option points."""


class AiSuggestion(BaseModel):
    path: str
    value: Any = None
    reason: str = ""


class AiTuning(BaseModel):
    analysis: str = ""
    suggestions: list[AiSuggestion]


# Small local models sometimes reply with nothing or with prose; ask again before giving up.
ATTEMPTS = 3
REMINDER = (
    "Your last reply could not be read. Reply with one JSON object only: "
    '{"analysis": "...", "suggestions": [{"path": "...", "value": ..., "reason": "..."}]}'
)
NO_ANALYSIS = "The AI model suggested these changes without writing an analysis."


def _objects(text: str) -> list:
    """JSON values written one after another, separated by commas, newlines or spaces."""
    decoder = json.JSONDecoder()
    found, position = [], 0
    while True:
        while position < len(text) and text[position] in " \t\r\n,":
            position += 1
        if position >= len(text):
            break
        try:
            value, position = decoder.raw_decode(text, position)
        except json.JSONDecodeError as exc:
            raise ValueError("not JSON") from exc
        found.append(value)
    if not found:
        raise ValueError("empty reply")
    return found


def parse_tuning(text: str) -> AiTuning:
    """Read the model's reply, forgiving the shapes small local models often produce.

    Accepts the requested object, a bare list of suggestions, or suggestions written one
    after another, by commas or by lines, without the surrounding brackets. Raises ValueError
    when none fit."""
    # Drop code fences and list markers ("- ", "* ", "1. ") the model may wrap suggestions in.
    text = re.sub(r"```(?:json)?", "", text)
    text = re.sub(r"(?m)^\s*(?:[-*\u2022]|\d+[.)])\s+", "", text).strip()
    if not text:
        raise ValueError("empty reply")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = _objects(text)
    if isinstance(data, dict) and "suggestions" not in data and "path" in data:
        data = [data]
    if isinstance(data, list):
        data = {"suggestions": data}
    try:
        parsed = AiTuning.model_validate(data)
    except ValidationError as exc:
        raise ValueError(str(exc.errors()[0]["msg"])) from exc
    if not parsed.suggestions:
        raise ValueError("no suggestions")
    return parsed


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
    chosen: list[str] | None = None,
) -> Suggestions:
    only = (
        [
            {"path": item["path"], "setting": item["label"], "now": item["value"]}
            for item in tunable_params(spec)
            if allows(item["path"], chosen)
        ]
        if chosen
        else None
    )
    user = json.dumps(
        {
            "suggest": count,
            "only_change": only,
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
    parsed: AiTuning | None = None
    for attempt in range(1, ATTEMPTS + 1):
        message = user if attempt == 1 else f"{user}\n\n{REMINDER}"
        started = time.monotonic()
        text, status, tokens = "", UNAVAILABLE, (0, 0)
        try:
            reply = client.complete(system, message, AiTuning.model_json_schema())
            text, tokens = reply.text, (reply.input_tokens, reply.output_tokens)
            parsed = parse_tuning(text)
            status = OK
        except LLMError as exc:
            text = str(exc)
            raise
        except ValueError:
            log.warning("Unreadable tuning reply, attempt %s of %s", attempt, ATTEMPTS)
        finally:
            ai_log.record(
                ts=now.isoformat(),
                day=now.date().isoformat(),
                kind="strategy_tuning",
                key=spec.id,
                model=client.model,
                status=status,
                prompt=f"{system}\n\n{message}",
                output=text,
                payload=None,
                input_tokens=tokens[0],
                output_tokens=tokens[1],
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        if parsed is not None:
            break
    if parsed is None:
        raise LLMError(
            f"The AI model returned suggestions in an unreadable format, {ATTEMPTS} times."
        )
    changes = [Change(s.path, s.value, s.reason[:300]) for s in parsed.suggestions[: count * 2]]
    return Suggestions((parsed.analysis.strip() or NO_ANALYSIS)[:1200], changes, "ai")


# ---- Scoring and the tree -----------------------------------------------------------------


def design_feedback(node: dict) -> dict:
    """What the AI may read about a version: design-session results only."""
    return {
        **{key: node["design"].get(key) for key in SUMMARY_KEYS},
        "why_days_were_skipped": node["design"].get("blocked") or {},
        "exit_reasons": node.get("design_exits") or {},
        "average_holding_days": node["design"].get("average_holding_days"),
    }


def trade_timing(trades: list[dict]) -> list[dict]:
    """Trades, wins and net P&L by weekday (0 is Monday) and the IST hour of entry.

    Small enough to keep for every version a tuning run tests, unlike the trades."""
    cells: dict[tuple[int, int], dict] = {}
    for trade in trades:
        stamp = trade.get("opened_at") or trade.get("opened")
        if not stamp:
            continue
        try:
            opened = datetime.fromisoformat(str(stamp))
        except ValueError:
            continue
        if opened.tzinfo is not None:
            opened = opened.astimezone(IST)
        # A daily decision has no time of day; -1 keeps it apart from real hours.
        hour = opened.hour if "T" in str(stamp) else -1
        cell = cells.setdefault(
            (opened.weekday(), hour),
            {"day": opened.weekday(), "hour": hour, "trades": 0, "wins": 0, "pnl": 0.0},
        )
        pnl = float(trade.get("net_pnl") or 0.0)
        cell["trades"] += 1
        cell["wins"] += int(pnl > 0)
        cell["pnl"] = round(cell["pnl"] + pnl, 2)
    return sorted(cells.values(), key=lambda c: (c["day"], c["hour"]))


def _exit_reasons(trades: list[dict]) -> dict[str, int]:
    counts = Counter(
        (trade.get("exit_reason") or "unknown").split(":")[0].split(" for ")[0] for trade in trades
    )
    return dict(counts.most_common(6))


class Scorer:
    """Backtests versions on the design and unseen sessions, in parallel when configured."""

    def __init__(
        self,
        data_dir: Path,
        config_dir: Path,
        windows: LadderWindows,
        now: datetime,
        progress: Callable[[int, int], None] | None = None,
    ):
        self.data_dir, self.config_dir, self.windows, self.now = data_dir, config_dir, windows, now
        self.progress = progress
        self.sessions = History(data_dir).sessions
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
        runs = iter(run_tasks(tasks, self.config.lab.workers, self.sessions, self.progress))
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
                "design_timing": trade_timing(design_run["trades"]),
                "validate_timing": trade_timing(validate_run["trades"]),
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


@dataclass
class Picker:
    """Turns suggested changes into untried child versions, up to a round's count."""

    parent: StrategySpec
    root_name: str
    depth: int
    seen: set[str]
    count: int
    # The settings the owner chose to tune; None allows every setting.
    chosen: list[str] | None = None

    def take(
        self,
        changes: list[Change],
        children: list[tuple[Change, StrategySpec]],
        skipped: list[str] | None,
    ) -> None:
        """Add valid, untried changes to `children`; note refused ones in `skipped` if given."""
        for change in changes:
            if len(children) == self.count:
                return
            if not allows(change.path, self.chosen):
                if skipped is not None:
                    skipped.append(f"{change.path}: not among the settings chosen to tune")
                continue
            changed, label = apply_change(self.parent, change)
            if changed is None:
                if skipped is not None:
                    skipped.append(label)
                continue
            child = make_child(self.parent, self.root_name, self.depth, label, changed)
            mark = signature(child)
            if mark in self.seen:
                if skipped is not None:
                    skipped.append(f"{label}: already tried")
                continue
            self.seen.add(mark)
            children.append((change, child))


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
        children: list[tuple[Change, StrategySpec]] = []
        chosen = session.get("params") or None
        pick = Picker(
            parent_spec, session["root_name"], parent["depth"] + 1, seen, per_round, chosen
        )
        pick.take(suggestions.changes, children, record["skipped"])
        asks = 0
        while len(children) < per_round and suggestions.source == "ai" and asks < EXTRA_ASKS:
            asks += 1
            short = per_round - len(children)
            note(f"Round {number}: asking the AI model for {short} more changes")
            pending = [
                {"change": child.step, "kept": None, "design_trades": None, "design_net_pnl": None}
                for _, child in children
            ]
            suggestions = suggest(
                parent_spec, design_feedback(parent), tried_history(session) + pending, short
            )
            record["skipped"].extend(suggestions.notes)
            pick.take(suggestions.changes, children, record["skipped"])
        if len(children) < per_round:
            before = len(children)
            sweep = [
                Change(path, value, "try another value of a chosen setting")
                for path, value in sweep_changes(parent_spec, chosen)
            ]
            fill = built_in_changes(parent_spec) + more_changes(parent_spec) + sweep
            pick.take(fill, children, None)
            record["built_in_added"] = len(children) - before
        if len(children) < per_round:
            record["skipped"].append(
                f"Only {len(children)} untried changes were left for this version"
            )
        if not children:
            note(f"Round {number}: no untried change is left, so tuning stops here")
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
            parent = kept[0]
            session["best_id"] = parent["id"]
        else:
            note(f"Round {number}: no change was kept, so the next round tries others on it")
    session["trials"] = session.get("trials", 0) + scorer.trials
    scorer.trials = 0
    return session


def _curve(daily: dict[str, float] | None) -> list[dict]:
    """Cumulative P&L by day, for comparing a change with its parent."""
    total, curve = 0.0, []
    for day in sorted(daily or {}):
        total += daily[day]
        curve.append({"day": day, "pnl": round(total, 2)})
    return curve


def public(session: dict) -> dict:
    """The session for the page: each version's unseen P&L as a curve instead of daily rows."""
    nodes = {
        key: {k: v for k, v in node.items() if k != "validate_daily"}
        | {"validate_curve": _curve(node.get("validate_daily"))}
        for key, node in session["nodes"].items()
    }
    return clean({**session, "nodes": nodes})

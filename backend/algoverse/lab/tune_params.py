"""The settings of a strategy the tuner may change, for the owner to choose from.

Each setting has a dotted path, the same paths tuning changes use. Choosing a path allows
changes to it and to anything inside it: choosing "entry.rules.0" lets the tuner change any
part of the first entry rule, or remove it. Paths ending in ".+" allow adding one new item,
which only the AI model suggests.
"""

from __future__ import annotations

from datetime import time
from typing import Any

from algoverse.lab.short_names import short_operand, short_rule
from algoverse.lab.spec import StrategySpec
from algoverse.lab.technical import INDICATORS

ADD_ITEMS = (
    ("entry.rules.+", "Entry", "Add an entry rule"),
    ("entry.conditions.+", "Entry", "Add a market condition"),
    ("exits.rules.+", "Exits", "Add an exit rule"),
)
# Nudges for a chosen number when the tuner needs more changes to fill a round.
SCALES = (0.9, 1.1, 0.8, 1.2, 0.7, 1.3, 0.6, 1.5, 0.5, 1.75, 2.0, 0.4, 2.5, 0.3, 3.0)
STEPS = (1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 6, -6, 8, -8, 10, -10)


def allows(path: str, chosen: list[str] | None) -> bool:
    """True when no choice was made, or the path is a chosen setting or inside one."""
    if not chosen:
        return True
    path = path.strip()
    return any(path == item or path.startswith(f"{item}.") for item in chosen)


def _show(value: Any) -> str:
    if value is None:
        return "off"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "any"
    if isinstance(value, time):
        return value.strftime("%H:%M")
    return str(value)


def _item(path: str, group: str, label: str, value: Any, kind: str) -> dict:
    number = isinstance(value, int | float) and not isinstance(value, bool)
    return {
        "path": path,
        "group": group,
        "label": label,
        "value": _show(value),
        "kind": kind,
        "current": value if number else None,
    }


def _child(parent: str, rule_name: str, item: dict) -> dict:
    """A setting inside a rule: listed under the rule, and named with it when alone, as in
    "Rule 4 value side: First 15m close · first minutes"."""
    label = item["label"]
    return {**item, "parent": parent, "full": f"{rule_name} {label[0].lower()}{label[1:]}"}


def _kind(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return "other"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "number"
    return "other"


def tunable_params(spec: StrategySpec) -> list[dict]:
    """Every setting tuning can change in this strategy, grouped and labelled for the owner."""
    exits, entry = spec.exits, spec.entry
    unit = {"premium": "× premium", "rupees": "₹ per lot", "points": "points"}
    items = [
        _item(
            "exits.target_frac",
            "Exits",
            f"Take profit ({unit[exits.target_unit]})",
            exits.target_frac,
            "number",
        ),
        _item(
            "exits.stop_frac",
            "Exits",
            f"Stop loss ({unit[exits.stop_unit]})",
            exits.stop_frac,
            "number",
        ),
        _item(
            "exits.trail",
            "Exits",
            f"Trailing stop ({unit[exits.trail_unit]})",
            exits.trail,
            "number",
        ),
    ]
    if exits.trail is not None:
        items.append(
            _item("exits.trail_step", "Exits", "Trailing step", exits.trail_step, "number")
        )
    items += [
        _item(
            "exits.breakeven_at",
            "Exits",
            f"Move stop to cost at ({unit[exits.breakeven_unit]})",
            exits.breakeven_at,
            "number",
        ),
        _item("exits.exit_dte", "Exits", "Close days before expiry", exits.exit_dte, "int"),
        _item("exits.daily_stop", "Exits", "Daily stop loss, ₹", exits.daily_stop, "number"),
        _item("exits.daily_target", "Exits", "Daily take profit, ₹", exits.daily_target, "number"),
    ]
    if spec.decide_every != "day":
        items.append(
            _item("exits.square_off", "Timing", "Square off by", exits.square_off, "other")
        )
        items += [
            _item("entry.time_from", "Timing", "Trade from", entry.time_from, "other"),
            _item("entry.time_to", "Timing", "Trade until", entry.time_to, "other"),
        ]
    items += [
        _item("decide_every", "Timing", "Decide every", spec.decide_every, "other"),
        _item("entry.dte_min", "Entry", "Days to expiry, min", entry.dte_min, "int"),
        _item("entry.dte_max", "Entry", "Days to expiry, max", entry.dte_max, "int"),
        _item(
            "entry.max_trades_per_day",
            "Entry",
            "Max trades per day",
            entry.max_trades_per_day,
            "int",
        ),
        _item("entry.trend", "Entry", "Trend", entry.trend, "other"),
        _item("entry.iv_rank_min", "Entry", "IV rank, min", entry.iv_rank_min, "number"),
        _item("entry.iv_rank_max", "Entry", "IV rank, max", entry.iv_rank_max, "number"),
    ]
    by = {"delta": "delta", "atm_offset": "strikes from ATM", "premium": "premium ₹"}
    for index, leg in enumerate(spec.legs):
        name = f"Leg {index + 1} {leg.action} {leg.option_type}"
        items += [
            _item(
                f"legs.{index}.value",
                "Legs",
                f"{name} · strike ({by[leg.select]})",
                leg.value,
                _kind(leg.value),
            ),
            _item(f"legs.{index}.stop", "Legs", f"{name} · stop loss", leg.stop, "number"),
            _item(f"legs.{index}.target", "Legs", f"{name} · take profit", leg.target, "number"),
        ]
    for index, condition in enumerate(entry.conditions):
        items.append(
            _item(
                f"entry.conditions.{index}.value",
                "Market conditions",
                f"{condition.signal} {condition.op}",
                condition.value,
                "number",
            )
        )
    for section, group, title, rules in (
        ("entry.rules", "Entry rules", "Rule", entry.rules),
        ("entry.any_rules", "Entry rules", "Any-of rule", entry.any_rules),
        ("exits.rules", "Exit rules", "Exit rule", exits.rules),
    ):
        for index, rule in enumerate(rules):
            path = f"{section}.{index}"
            items.append(
                {
                    **_item(path, group, f"{title} {index + 1} {short_rule(rule)}", None, "rule"),
                    "value": "any part",
                }
            )
            if getattr(rule, "type", None) != "compare":
                continue
            for side, role in (("left", "Value side"), ("right", "Compared-with side")):
                operand = getattr(rule, side)
                if operand.kind == "value" and operand.value is not None:
                    items.append(
                        _child(
                            path,
                            f"{title} {index + 1}",
                            _item(
                                f"{path}.{side}.value",
                                group,
                                f"{role}: level",
                                operand.value,
                                "number",
                            ),
                        )
                    )
                if operand.kind != "indicator" or operand.name not in INDICATORS:
                    continue
                for param in INDICATORS[operand.name].params:
                    value = operand.params.get(param.name, param.default)
                    if isinstance(value, bool) or not isinstance(value, int | float):
                        continue
                    items.append(
                        _child(
                            path,
                            f"{title} {index + 1}",
                            _item(
                                f"{path}.{side}.params.{param.name}",
                                group,
                                f"{role}: {short_operand(operand)} · {param.label.lower()}",
                                value,
                                _kind(value),
                            ),
                        )
                    )
    for path, group, label in ADD_ITEMS:
        items.append({**_item(path, group, label, None, "add"), "value": "AI only"})
    return items


def sweep_changes(spec: StrategySpec, chosen: list[str] | None) -> list[tuple[str, Any]]:
    """More values for the chosen numbers, nearest first, so a round can be filled even when
    only a setting or two may change."""
    found = []
    for item in tunable_params(spec):
        if item["kind"] not in ("int", "number") or not allows(item["path"], chosen):
            continue
        current = item["current"]
        if current is None:
            continue
        if item["kind"] == "int" or (float(current).is_integer() and abs(current) >= 2):
            values = [int(round(current)) + step for step in STEPS]
            if item["kind"] == "number" and abs(current) >= 10:
                values = [int(round(current * scale)) for scale in SCALES] + values
        else:
            values = [round(current * scale, 2) for scale in SCALES]
        seen = {current}
        for value in values:
            if value not in seen:
                seen.add(value)
                found.append((item["path"], value))
    # Alternate between settings so one round tries several of them.
    by_path: dict[str, list] = {}
    for path, value in found:
        by_path.setdefault(path, []).append(value)
    ordered = []
    while any(by_path.values()):
        for path, values in by_path.items():
            if values:
                ordered.append((path, values.pop(0)))
    return ordered

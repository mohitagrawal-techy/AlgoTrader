"""A strategy's research dossier: fifteen points, every figure computed by code.

The AI model writes three things only, from the computed facts: the hypothesis, the
weaknesses it sees, and the next steps. It is told to use no number that is not in the facts.
Until it has written them, those points say so, and the code's own list of weaknesses stands.
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from pydantic import BaseModel, Field

from algoverse.ai.budget import AiLog
from algoverse.ai.client import LLMClient, LLMError
from algoverse.ai.schemas import OK, UNAVAILABLE
from algoverse.analytics.regimes import LABELS
from algoverse.lab.spec import StrategySpec, describe_entry, describe_legs, has_undefined_risk
from algoverse.lab.stages import rupees

SYSTEM = """You write three parts of a research dossier for a rule-based Indian index option \
strategy that has been backtested. The dossier is research documentation for the strategy's \
owner, not investment advice, and no real orders are placed.

You receive the computed facts as JSON. Write:
- hypothesis: two or three sentences on the market behaviour the rules try to exploit, why it \
might exist, and when it should stop working.
- weaknesses: three to six short points on what could make the result misleading or fragile.
- next_steps: three to five concrete research steps that would test the strategy further.

Use only numbers that appear in the facts, and quote them exactly. Do not invent results, \
dates or statistics. Never recommend trading it with real money; the strongest advice is \
"paper trade" when the stage allows it. Plain sentences, no headings or markdown."""


class DossierNotes(BaseModel):
    hypothesis: str = Field(max_length=1200)
    weaknesses: list[str] = Field(max_length=8)
    next_steps: list[str] = Field(max_length=8)


def _pct(value, digits: int = 1) -> str:
    return "–" if value is None else f"{value:.{digits}f}%"


def _num(value, digits: int = 2) -> str:
    return "–" if value is None else f"{value:.{digits}f}"


def _exit_text(spec: StrategySpec) -> list[str]:
    exits = spec.exits
    unit = {"premium": "of the premium", "rupees": "rupees per lot", "points": "option points"}

    def amount(value, kind):
        return f"{value:g}x {unit[kind]}" if kind == "premium" else f"{value:g} {unit[kind]}"

    lines = [
        f"Profit target {amount(exits.target_frac, exits.target_unit)}",
        f"Stop loss {amount(exits.stop_frac, exits.stop_unit)}",
        f"Time exit {exits.exit_dte} calendar days before expiry",
    ]
    if exits.trail is not None:
        lines.append(f"Trailing stop {amount(exits.trail, exits.trail_unit)}")
    if exits.breakeven_at is not None:
        lines.append(f"Stop to cost after {amount(exits.breakeven_at, exits.breakeven_unit)}")
    if exits.square_off is not None:
        lines.append(f"Square off every day by {exits.square_off:%H:%M}")
    if exits.rules:
        lines.append(f"{len(exits.rules)} indicator exit rule(s)")
    if exits.daily_stop is not None:
        lines.append(f"Stop for the day after a loss of {rupees(exits.daily_stop)}")
    if exits.daily_target is not None:
        lines.append(f"Stop for the day after a gain of {rupees(exits.daily_target)}")
    return lines


def _weaknesses(spec, result, stage, regimes) -> list[str]:
    """What the code can see that makes the evidence weaker."""
    found = []
    test, train = result.get("test") or {}, result.get("train") or {}
    for check in stage.get("checks", []):
        if not check["passed"]:
            found.append(f"Failed check: {check['label'].lower()} ({check['detail']}).")
    if (test.get("sharpe") or 0) < (train.get("sharpe") or 0) / 2 and (
        train.get("sharpe") or 0
    ) > 0:
        found.append(
            f"Unseen Sharpe {_num(test.get('sharpe'))} is under half the training Sharpe "
            f"{_num(train.get('sharpe'))}, a sign of fitting to the training sessions."
        )
    if (test.get("longest_losing_streak") or 0) >= 5:
        found.append(
            f"A run of {test['longest_losing_streak']} losing trades in the unseen period."
        )
    if (test.get("capital_use_pct") or 0) > 80:
        found.append(f"Peak capital use {_pct(test['capital_use_pct'], 0)} leaves little room.")
    trades = ((result.get("detail") or {}).get("test") or {}).get("trades") or []
    net = sum(t.get("net_pnl") or 0 for t in trades)
    if trades and net > 0:
        best = max(t.get("net_pnl") or 0 for t in trades)
        if best / net > 0.5:
            found.append(
                f"One trade made {best / net:.0%} of the unseen profit, so the result rests on it."
            )
    if regimes:
        trend_rows = [r for r in regimes["rows"] if r["group"] == "trend" and r["sessions"]]
        total = sum(r["sessions"] for r in trend_rows)
        if total:
            top = max(trend_rows, key=lambda r: r["sessions"])
            if top["sessions"] / total > 0.6:
                found.append(
                    f"{top['sessions'] / total:.0%} of tested sessions were "
                    f"{top['label'].lower()}, so other markets are barely tested."
                )
    if has_undefined_risk(spec):
        found.append("Its loss has no cap.")
    return found


def build(
    spec: StrategySpec,
    result: dict | None,
    stage: dict,
    regimes: dict | None,
    search: dict | None,
    notes: dict | None,
) -> dict:
    """The fifteen points. `notes` are the AI model's, when written for this result."""
    result = result or {}
    test, train = result.get("test") or {}, result.get("train") or {}
    doubled = result.get("test_2x") or {}
    windows = result.get("windows") or {}
    evidence = result.get("evidence") or {}
    measured = stage.get("significance") or {}
    budget = spec.budget
    ai = notes or {}
    waiting = "Not written yet. Ask the AI model to write the dossier notes."
    code_weak = _weaknesses(spec, result, stage, regimes) if result else []

    def metrics(window: dict) -> list[list[str]]:
        return [
            ["Trades", str(window.get("trades", 0))],
            ["Net P&L", rupees(window.get("net_pnl"))],
            [
                "Win rate",
                _pct(None if window.get("win_rate") is None else window["win_rate"] * 100, 0),
            ],
            ["Profit factor", _num(window.get("profit_factor"))],
            ["Expectancy", rupees(window.get("expectancy"))],
            ["Sharpe", _num(window.get("sharpe"))],
            ["Sortino", _num(window.get("sortino"))],
            [
                "Max drawdown",
                f"{rupees(window.get('max_drawdown'))} ({_pct(window.get('max_drawdown_pct'))})",
            ],
            ["Recovery factor", _num(window.get("recovery_factor"))],
        ]

    sections = [
        {
            "title": "Identity",
            "source": "code",
            "items": [
                ["Name", spec.name],
                ["Id", spec.id],
                ["Source", spec.source],
                ["Bias", spec.bias],
                ["Created", spec.created_at or "–"],
            ],
        },
        {
            "title": "Hypothesis",
            "source": "ai",
            "text": ai.get("hypothesis") or waiting,
            "note": f"Owner's rationale: {spec.rationale}" if spec.rationale else None,
        },
        {
            "title": "Market and timeframe",
            "source": "code",
            "items": [
                ["Underlyings", ", ".join(spec.symbols) or "Every configured index"],
                [
                    "Decisions",
                    "Once a day at the close"
                    if spec.decide_every == "day"
                    else f"Every {spec.decide_every}",
                ],
            ],
        },
        {
            "title": "Structure",
            "source": "code",
            "text": describe_legs(spec),
            "items": [["Loss capped", "No" if has_undefined_risk(spec) else "Yes"]],
        },
        {"title": "Entry rules", "source": "code", "text": describe_entry(spec)},
        {"title": "Exit rules", "source": "code", "bullets": _exit_text(spec)},
        {
            "title": "Sizing and risk budget",
            "source": "code",
            "items": [
                ["Capital", rupees(budget.capital)],
                [
                    "Risk per trade",
                    f"{budget.risk_per_trade_pct:g}% ({rupees(budget.risk_per_trade)})",
                ],
                [
                    "Sizing",
                    f"Spend up to {budget.deploy_pct:g}% of capital"
                    if budget.sizing == "budget"
                    else "By risk per trade",
                ],
                [
                    "Volatility sizing",
                    "Off"
                    if budget.vol_sizing == "off"
                    else f"ATR, scale {budget.vol_floor:g} to {budget.vol_cap:g}",
                ],
                [
                    "Circuit breakers",
                    ", ".join(
                        part
                        for part in (
                            f"{budget.pause_after_losses} losses in a row"
                            if budget.pause_after_losses
                            else "",
                            f"{budget.pause_drawdown_pct:g}% drawdown"
                            if budget.pause_drawdown_pct
                            else "",
                        )
                        if part
                    )
                    + f", pause {budget.pause_sessions} weekdays"
                    if budget.pause_after_losses or budget.pause_drawdown_pct
                    else "Off",
                ],
                ["Open positions at most", str(budget.max_open)],
            ],
        },
        {
            "title": "Test design",
            "source": "code",
            "items": [
                [
                    "Training",
                    f"{windows.get('train_start', '–')} to {windows.get('train_end', '–')}, "
                    f"{windows.get('train_sessions', '–')} sessions",
                ],
                [
                    "Unseen test",
                    f"{windows.get('test_start', '–')} to {windows.get('test_end', '–')}, "
                    f"{windows.get('test_sessions', '–')} sessions",
                ],
                ["Tested", str(result.get("evaluated_at") or "Not yet")],
                ["Fills", "Next bar, at modelled bid and ask with slippage"],
            ],
        },
        {
            "title": "Costs and slippage",
            "source": "code",
            "items": [
                ["Unseen costs paid", rupees(test.get("costs"))],
                ["Unseen net at 2x slippage", rupees(doubled.get("net_pnl"))],
                ["Cost model", "Brokerage, STT, exchange, SEBI, stamp duty and GST, dated"],
            ],
        },
        {
            "title": "Performance",
            "source": "code",
            "table": {
                "columns": ["", "Training", "Unseen test"],
                "rows": [
                    [label, value, other]
                    for (label, value), (_, other) in zip(
                        metrics(train), metrics(test), strict=True
                    )
                ],
            },
        },
        {
            "title": "Statistical evidence",
            "source": "code",
            "items": [
                [
                    "Luck-adjusted profit per trade",
                    rupees(evidence.get("floor")) if evidence else "–",
                ],
                ["Probabilistic Sharpe (vs 0)", _num(measured.get("psr"))],
                ["Deflated Sharpe", _num(measured.get("dsr"))],
                ["Sharpe luck alone would show", _num(measured.get("luck_sharpe"))],
                ["Distinct rule sets tried", str(measured.get("trials", "–"))],
                [
                    "Parameter search",
                    "None"
                    if not search
                    else f"{search['method']}, {search['trials']} settings; "
                    f"{search['neighbour_share']:.0%} of neighbours hold up"
                    f" ({'robust' if search['robust'] else 'a lone peak'})",
                ],
            ],
        },
        {
            "title": "Consistency",
            "source": "code",
            "table": {
                "columns": ["Period", "Sessions", "Trades", "Net P&L"],
                "rows": [
                    [
                        f"{f['start']} to {f['end']}",
                        str(f["sessions"]),
                        str(f["trades"]),
                        rupees(f["net_pnl"]),
                    ]
                    for f in stage.get("folds", [])
                ],
            },
        },
        {
            "title": "Market regimes",
            "source": "code",
            "items": []
            if not regimes
            else [
                ["Suits", ", ".join(LABELS[k][1] for k in regimes["suits"]) or "None proven"],
                ["Avoid", ", ".join(LABELS[k][1] for k in regimes["avoid"]) or "None proven"],
            ],
            "table": None
            if not regimes
            else {
                "columns": ["Regime", "Sessions", "Trades", "Net P&L", "Per trade"],
                "rows": [
                    [
                        r["label"],
                        str(r["sessions"]),
                        str(r["trades"]),
                        rupees(r["net_pnl"]),
                        rupees(r["expectancy"]),
                    ]
                    for r in regimes["rows"]
                    if r["trades"] or r["sessions"]
                ],
            },
        },
        {
            "title": "Weaknesses",
            "source": "mixed",
            "bullets": [*code_weak, *(ai.get("weaknesses") or [])]
            or ["None found by the code checks."],
            "note": None if notes else waiting,
        },
        {
            "title": "Stage and next steps",
            "source": "mixed",
            "text": f"{(stage.get('stage') or 'untested').replace('_', ' ').capitalize()}: "
            f"{stage.get('reason', '')}",
            "bullets": ai.get("next_steps") or [],
            "note": None if notes else waiting,
        },
    ]
    for number, section in enumerate(sections, start=1):
        section["number"] = number
    return {"sections": sections, "notes_written_at": (notes or {}).get("written_at")}


def facts(dossier: dict) -> dict:
    """The computed sections as plain facts for the AI model, leaving out its own parts."""
    return {
        section["title"]: {
            key: section[key] for key in ("text", "items", "bullets", "table") if section.get(key)
        }
        for section in dossier["sections"]
        if section["source"] != "ai"
    }


def write_notes(
    client: LLMClient, ai_log: AiLog, now: datetime, spec: StrategySpec, dossier: dict
) -> dict:
    user = json.dumps(facts(dossier), default=str)
    started = time.monotonic()
    text, status, tokens = "", UNAVAILABLE, (0, 0)
    try:
        reply = client.complete(SYSTEM, user, DossierNotes.model_json_schema())
        text, tokens = reply.text, (reply.input_tokens, reply.output_tokens)
        notes = DossierNotes.model_validate_json(text)
        status = OK
    except LLMError as exc:
        text = str(exc)
        raise
    except ValueError as exc:
        raise LLMError("The AI model returned the dossier notes in an unreadable format.") from exc
    finally:
        ai_log.record(
            ts=now.isoformat(),
            day=now.date().isoformat(),
            kind="strategy_dossier",
            key=spec.id,
            model=client.model,
            status=status,
            prompt=f"{SYSTEM}\n\n{user}",
            output=text,
            payload=None,
            input_tokens=tokens[0],
            output_tokens=tokens[1],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    return notes.model_dump() | {"model": client.model, "written_at": now.isoformat()}


def markdown(spec: StrategySpec, dossier: dict) -> str:
    """The dossier as a Markdown document."""
    out = [f"# Research dossier: {spec.name}", ""]
    out.append(
        "Research documentation from backtests, not investment advice. Figures are computed "
        "by code; the hypothesis, weaknesses and next steps marked AI were written by an AI "
        "model from those figures."
    )
    for section in dossier["sections"]:
        tag = {"code": "", "ai": " (AI)", "mixed": " (code and AI)"}[section["source"]]
        out += ["", f"## {section['number']}. {section['title']}{tag}", ""]
        if section.get("text"):
            out += [section["text"], ""]
        for label, value in section.get("items") or []:
            out.append(f"- **{label}:** {value}")
        if section.get("items"):
            out.append("")
        for bullet in section.get("bullets") or []:
            out.append(f"- {bullet}")
        table = section.get("table")
        if table and table["rows"]:
            out += ["", "| " + " | ".join(table["columns"]) + " |"]
            out.append("|" + "---|" * len(table["columns"]))
            out += ["| " + " | ".join(row) + " |" for row in table["rows"]]
        if section.get("note"):
            out += ["", f"_{section['note']}_"]
    return "\n".join(out).rstrip() + "\n"

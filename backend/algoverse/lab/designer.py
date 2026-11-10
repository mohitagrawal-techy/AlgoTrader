"""Asks the AI model to propose strategy definitions. Nothing here can place a trade."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from pydantic import BaseModel, ValidationError

from algoverse.ai.budget import AiLog
from algoverse.ai.client import LLMClient, LLMError
from algoverse.ai.schemas import OK, UNAVAILABLE
from algoverse.lab.spec import (
    EntryRules,
    ExitRules,
    LegSpec,
    StrategySpec,
    describe_legs,
    make_id,
    signature,
)

log = logging.getLogger(__name__)
MAX_GUIDANCE = 500
MAX_FEEDBACK = 24

SYSTEM = """You design rule-based option strategies for Indian index options (NIFTY, BANKNIFTY, \
FINNIFTY). A backtester will test every strategy you propose, so propose testable hypotheses, \
not predictions. You are not giving investment advice.

Each strategy has:
- name: short and descriptive.
- rationale: one or two sentences on the edge it tries to capture and when it should fail.
- bias: bullish, bearish or neutral.
- legs: 1 to 4. Each has action (buy/sell), option_type (CE/PE), ratio (1-4), expiry (0 = the \
entry expiry, 1 = the next expiry), and a strike rule: select "delta" with value 0.05-0.95 \
(absolute delta), or select "atm_offset" with a whole number of listed strikes from the money \
(0 = at the money, positive = higher strikes).
- entry: trend (any of up, down, range; empty list = any), iv_rank_min and iv_rank_max (0-100 or \
null), dte_min and dte_max (calendar days to the entry expiry, 1-45).
- exits: target_frac and stop_frac (fractions of the premium paid or received, 0.05-5), exit_dte \
(days before expiry for a time exit, 0-10, and less than dte_min).

How the test works: one position per underlying at a time, entered at the next day's open with \
realistic spreads and Indian taxes and fees. The account settings and loss limit are in the user \
message. A structure whose loss at the stop is above that limit cannot trade, so prefer defined \
risk with wings close enough to fit it. Structures that sell more options than they buy are \
usually blocked.

The user message lists strategies already tested, with results from the TRAINING period only. \
Learn from them: build on what worked, fix what was blocked, and do not repeat a tested rule \
set. Small samples are noise, so do not chase a result with few trades. Propose varied ideas."""


class DesignedStrategy(BaseModel):
    name: str
    rationale: str
    bias: str
    legs: list[LegSpec]
    entry: EntryRules
    exits: ExitRules


class DesignOutput(BaseModel):
    strategies: list[DesignedStrategy]


def feedback_row(spec: StrategySpec, result: dict | None) -> dict:
    """What the designer may see about a tested strategy. Never includes test-period results."""
    row = {
        "name": spec.name,
        "legs": describe_legs(spec),
        "entry": spec.entry.model_dump(exclude_none=True),
        "exits": spec.exits.model_dump(),
    }
    if result is not None:
        train = result["train"]
        row["training_result"] = {
            key: train.get(key)
            for key in ("trades", "net_pnl", "win_rate", "expectancy", "sharpe", "max_drawdown")
        }
        row["why_days_were_skipped"] = train.get("blocked") or {}
    return row


def design_strategies(
    client: LLMClient,
    ai_log: AiLog,
    now: datetime,
    count: int,
    round_number: int,
    rounds: int,
    account: dict,
    tested: list[dict],
    known_signatures: set[str],
    guidance: str = "",
) -> tuple[list[StrategySpec], list[str]]:
    """Return new valid strategies and notes on any proposals that were dropped."""
    user = json.dumps(
        {
            "round": round_number,
            "of_rounds": rounds,
            "propose": count,
            "account": account,
            "owner_guidance": guidance[:MAX_GUIDANCE] or None,
            "already_tested": tested[-MAX_FEEDBACK:],
        },
        default=str,
    )
    started = time.monotonic()
    text, status, tokens = "", UNAVAILABLE, (0, 0)
    try:
        reply = client.complete(SYSTEM, user, DesignOutput.model_json_schema())
        text, tokens = reply.text, (reply.input_tokens, reply.output_tokens)
        proposals = json.loads(text).get("strategies", [])
        status = OK
    except LLMError as exc:
        text = str(exc)
        raise
    except (ValueError, AttributeError) as exc:
        raise LLMError("The AI model returned strategies in an unreadable format.") from exc
    finally:
        ai_log.record(
            ts=now.isoformat(),
            day=now.date().isoformat(),
            kind="strategy_design",
            key=f"round-{round_number}",
            model=client.model,
            status=status,
            prompt=f"{SYSTEM}\n\n{user}",
            output=text,
            payload=None,
            input_tokens=tokens[0],
            output_tokens=tokens[1],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    specs, notes, seen = [], [], set(known_signatures)
    for proposal in proposals[: count * 2]:
        name = str(proposal.get("name", "Unnamed"))[:60] if isinstance(proposal, dict) else "?"
        try:
            draft = StrategySpec.model_validate(
                {**proposal, "id": "draft", "source": "ai", "symbols": []}
            )
        except (ValidationError, TypeError) as exc:
            first = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else "bad shape"
            notes.append(f"{name}: dropped, {first}")
            continue
        mark = signature(draft)
        if mark in seen:
            notes.append(f"{name}: dropped, same rules as an existing strategy")
            continue
        seen.add(mark)
        specs.append(
            draft.model_copy(
                update={"id": make_id(draft.name, mark), "created_at": now.isoformat()}
            )
        )
        if len(specs) == count:
            break
    return specs, notes

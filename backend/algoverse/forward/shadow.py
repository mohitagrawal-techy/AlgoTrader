"""Follows ideas the system did not take, so each rejection layer can be measured."""

from __future__ import annotations

from dataclasses import replace

from algoverse.dashboard import idea_id
from algoverse.evaluation.pipeline import Idea
from algoverse.forward.gates import ShadowConfig
from algoverse.risk.limits import RiskDecision

AI_VETO = "ai_veto"
AI_APPROVED = "ai_approved"
RISK = "risk"
FILTER = "filter"
PASSED_OVER = "passed_over"
STAGES = (AI_VETO, AI_APPROVED, RISK, FILTER, PASSED_OVER)
VETO_PREFIX = "AI veto:"


def classify(idea: Idea) -> tuple[str, str]:
    """Return the stage that decided this idea, and the reason."""
    veto = next((r for r in idea.rejections if r.startswith(VETO_PREFIX)), None)
    if veto:
        return AI_VETO, veto
    if idea.rejections:
        return FILTER, idea.rejections[0]
    if idea.risk is not None and not idea.risk.approved:
        return RISK, idea.risk.reasons[0]
    if idea.ai is not None and idea.ai.status == "ok":
        return AI_APPROVED, "AI reviewed and did not veto"
    return PASSED_OVER, "Tradable but not selected"


def can_be_followed(idea: Idea) -> bool:
    """An idea already inside its exit window would close at once and measure nothing."""
    return idea.structure.dte > idea.plan.exit_dte and idea.structure.reward > 0


def as_one_lot(idea: Idea) -> Idea:
    """Remove the rejection so the normal paper fills can follow it, at one lot."""
    decision = RiskDecision(True, 1, idea.plan.planned_loss, idea.structure.margin, ())
    return replace(idea, rejections=(), risk=decision)


def select(
    ideas: list[Idea], already_seen: set[str], used_today: int, config: ShadowConfig
) -> list[tuple[Idea, str, str]]:
    """Choose new ideas to follow, taking turns between stages so none starves another."""
    room = min(config.max_new_per_cycle, config.max_new_per_day - used_today)
    if room <= 0:
        return []
    queues: dict[str, list[tuple[Idea, str]]] = {stage: [] for stage in STAGES}
    seen = set(already_seen)
    for idea in sorted(ideas, key=lambda item: -(item.final_score or 0.0)):
        key = idea_id(idea)
        if key not in seen and can_be_followed(idea):
            seen.add(key)
            stage, reason = classify(idea)
            queues[stage].append((idea, reason))
    chosen: list[tuple[Idea, str, str]] = []
    while len(chosen) < room and any(queues.values()):
        for stage in STAGES:
            if queues[stage] and len(chosen) < room:
                idea, reason = queues[stage].pop(0)
                chosen.append((idea, stage, reason))
    return chosen

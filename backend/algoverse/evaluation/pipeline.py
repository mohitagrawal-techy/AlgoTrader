"""Turns market context into ranked, risk-checked ideas."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algoverse.ai.schemas import OK, AiReview
from algoverse.evaluation.config import ScoringConfig
from algoverse.evaluation.context import MarketContext
from algoverse.evaluation.filters import filter_reasons
from algoverse.evaluation.params.base import ParamInput
from algoverse.evaluation.scorer import ScoreCard, Scorer
from algoverse.evaluation.structure import StructureMetrics, analyse
from algoverse.risk.config import RiskConfig
from algoverse.risk.costs import CostModel
from algoverse.risk.limits import (
    ExitPlan,
    PortfolioState,
    RiskDecision,
    evaluate_risk,
    make_exit_plan,
)
from algoverse.risk.stress import StressResult, stress_test
from algoverse.strategies.base import Candidate
from algoverse.strategies.generate import generate_candidates


@dataclass(frozen=True)
class Idea:
    candidate: Candidate
    structure: StructureMetrics
    card: ScoreCard
    plan: ExitPlan
    stress: StressResult
    rejections: tuple[str, ...]
    risk: RiskDecision | None
    ctx_spot: float = 0.0
    ai: AiReview | None = None

    @property
    def tradable(self) -> bool:
        return not self.rejections and self.risk is not None and self.risk.approved

    @property
    def final_score(self) -> float | None:
        """The numeric score after any AI penalty. The AI can only lower it."""
        composite = self.card.composite
        if composite is None:
            return None
        penalty = min(self.ai.adjustment, 0) if self.ai and self.ai.status == OK else 0
        return max(composite + penalty, 0.0)


def rank_key(idea: Idea) -> tuple:
    return (not idea.tradable, -(idea.final_score or 0.0))


def generate_ideas(
    ctx: MarketContext,
    chain: pd.DataFrame,
    scoring: ScoringConfig,
    risk: RiskConfig,
    costs: CostModel,
    portfolio: PortfolioState,
    custom=None,
) -> list[Idea]:
    """Evaluate every candidate. Rejected ideas are kept, with reasons, after tradable ones.

    `custom` supplies user-defined or AI-designed strategies, each with its entry-rule failures.
    """
    scorer = Scorer(scoring.profiles)
    ideas = []
    candidates: list[tuple[Candidate, list[str]]] = []
    if custom is None or not custom.only:
        built_in = generate_candidates(ctx.symbol, chain, scoring.generation)
        candidates += [(candidate, []) for candidate in built_in]
    if custom is not None:
        candidates += custom.candidates(ctx, chain)
    for candidate, entry_failures in candidates:
        structure = analyse(candidate, ctx, risk, costs, scoring.forecast_iv_weight)
        reasons = entry_failures + filter_reasons(candidate, structure, ctx, scoring.filters, risk)
        card = scorer.score(ParamInput(candidate, structure, ctx))
        if card.coverage < scoring.filters.min_coverage:
            reasons.append(f"only {card.coverage:.0%} of scoring weight has data")
        plan = make_exit_plan(candidate, structure, risk)
        stress = stress_test(candidate, risk.stress, ctx.r)
        decision = (
            None if reasons else evaluate_risk(candidate, structure, plan, stress, portfolio, risk)
        )
        ideas.append(Idea(candidate, structure, card, plan, stress, tuple(reasons), decision))
    return sorted(ideas, key=rank_key)

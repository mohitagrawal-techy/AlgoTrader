"""Hard filters applied before scoring."""

from __future__ import annotations

from algoverse.evaluation.config import FilterConfig
from algoverse.evaluation.context import MarketContext
from algoverse.evaluation.structure import StructureMetrics
from algoverse.risk.config import RiskConfig
from algoverse.strategies.base import Candidate


def filter_reasons(
    candidate: Candidate,
    structure: StructureMetrics,
    ctx: MarketContext,
    filters: FilterConfig,
    risk: RiskConfig,
) -> list[str]:
    """Return every reason the candidate fails. An empty list means it passes."""
    reasons: list[str] = []
    for expiry in sorted({leg.expiry for leg in candidate.legs}):
        if not ctx.by_expiry[expiry].forward_implied:
            reasons.append(f"forward could not be implied from quotes for {expiry}")
    for leg in candidate.legs:
        label = f"{leg.strike:g} {leg.option_type}"
        spread_pct = leg.spread / leg.mid * 100
        if spread_pct > filters.max_spread_pct and leg.spread > filters.max_spread_abs:
            reasons.append(f"{label}: spread {spread_pct:.1f}% is too wide")
        if not leg.oi >= filters.min_oi:
            reasons.append(f"{label}: open interest {leg.oi:g} is too low")
        if not leg.volume >= filters.min_volume:
            reasons.append(f"{label}: volume {leg.volume:g} is too low")

    rule = risk.exits[candidate.profile]
    # A position that squares off the same day may open on its time-exit day: the 15:00 time
    # exit or the square-off closes it that afternoon.
    inside = (
        structure.dte < rule.exit_dte
        if rule.square_off is not None
        else (structure.dte <= rule.exit_dte)
    )
    if inside:
        reasons.append(f"{structure.dte} days to expiry is inside the {rule.exit_dte}-day exit")
    if structure.dte > filters.max_dte:
        reasons.append(f"{structure.dte} days to expiry exceeds {filters.max_dte}")
    if structure.reward <= 0:
        reasons.append("no profit available within a 2-sigma move")
    elif structure.cost_ratio > filters.max_cost_ratio:
        reasons.append(f"costs are {structure.cost_ratio:.0%} of the reward")

    if candidate.profile in filters.block_on_high_events:
        end = candidate.front_expiry
        if not ctx.events.is_reviewed(end):
            reasons.append(f"event calendar is not reviewed through {end}")
        else:
            found = ctx.events.in_window(ctx.symbol, ctx.now.date(), end)
            reasons += [f"high-impact event: {e.name}" for e in found if e.severity == "high"]
    return reasons

"""Event-risk parameters."""

from __future__ import annotations

from algoverse.evaluation.params.base import ParamInput, param, scale


@param("event_clear", "events")
def event_clear(data: ParamInput):
    """Unavailable until the calendar has been reviewed through the holding period."""
    ctx, end = data.ctx, data.candidate.front_expiry
    if not ctx.events.is_reviewed(end):
        return None
    found = ctx.events.in_window(ctx.symbol, ctx.now.date(), end)
    if any(event.severity == "high" for event in found):
        score = 0.0
    elif found:
        score = 50.0
    else:
        score = 100.0
    return len(found), score, ", ".join(event.name for event in found)


@param("expiry_proximity", "events")
def expiry_proximity(data: ParamInput):
    dte = data.structure.dte
    return dte, scale(dte, 0, 30)

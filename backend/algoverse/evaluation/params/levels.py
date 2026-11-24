"""Support and resistance parameters. Only zones backed by two or more methods count."""

from __future__ import annotations

from algoverse.evaluation.params.base import ParamInput, param, scale

MIN_STRENGTH = 2


def _strong(data: ParamInput) -> tuple[list[float], list[float]]:
    """Distances to strong zones above and below spot, nearest first."""
    spot = data.ctx.spot
    centers = [z.center for z in data.stats.zones if z.strength >= MIN_STRENGTH]
    return sorted(c - spot for c in centers if c > spot), sorted(
        spot - c for c in centers if c < spot
    )


@param("level_room", "levels")
def level_room(data: ParamInput):
    move = data.stats.expected_move
    if not move:
        return None
    above, below = _strong(data)
    if data.bias == "bullish":
        distances = above
    elif data.bias == "bearish":
        distances = below
    else:
        distances = above + below
    if not distances:
        return None
    room = min(distances) / move
    return room, scale(room, 0, 1.5)


@param("support_proximity", "levels")
def support_proximity(data: ParamInput):
    """Closeness of the level a directional trade leans on."""
    move = data.stats.expected_move
    if not move or data.bias == "neutral":
        return None
    above, below = _strong(data)
    distances = below if data.bias == "bullish" else above
    if not distances:
        return None
    gap = distances[0] / move
    return gap, 100 - scale(gap, 0, 1)


@param("breakeven_vs_zones", "levels")
def breakeven_vs_zones(data: ParamInput):
    """Share of breakevens lying beyond the nearest strong zone on their side."""
    breakevens = data.structure.breakevens
    if not breakevens:
        return None
    spot = data.ctx.spot
    above, below = _strong(data)
    outside = 0
    for breakeven in breakevens:
        nearest = above if breakeven > spot else below
        if nearest and abs(breakeven - spot) >= nearest[0]:
            outside += 1
    share = outside / len(breakevens)
    return share, share * 100

"""Open-interest parameters."""

from __future__ import annotations

from algoverse.evaluation.params.base import ParamInput, by_bias, param, scale


@param("pcr_alignment", "positioning")
def pcr_alignment(data: ParamInput):
    pcr = data.stats.pcr
    return None if pcr is None else (pcr, by_bias(scale(pcr, 0.6, 1.4), data.bias))


@param("oi_buildup_alignment", "positioning")
def oi_buildup_alignment(data: ParamInput):
    buildup = data.stats.buildup
    return None if buildup is None else (buildup, by_bias((buildup + 1) * 50, data.bias))


@param("wall_room", "positioning")
def wall_room(data: ParamInput):
    """Distance to the nearest open-interest wall in the way, in expected moves."""
    stats = data.stats
    move = stats.expected_move
    if not move:
        return None
    above = [k - stats.forward for k in stats.walls["resistance"] if k > stats.forward]
    below = [stats.forward - k for k in stats.walls["support"] if k < stats.forward]
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


@param("max_pain_distance", "positioning")
def max_pain_distance(data: ParamInput):
    """Pull of max pain, signed by bias. Neutral structures want spot close to it."""
    stats = data.stats
    if stats.max_pain is None or not stats.expected_move:
        return None
    distance = (stats.max_pain - stats.forward) / stats.expected_move
    if data.bias == "neutral":
        return distance, 100 - scale(abs(distance), 0, 1)
    signed = distance if data.bias == "bullish" else -distance
    return distance, scale(signed, -1, 1)

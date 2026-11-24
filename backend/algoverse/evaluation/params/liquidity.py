"""Liquidity parameters. Each one is set by the structure's weakest leg."""

from __future__ import annotations

import math

from algoverse.evaluation.params.base import ParamInput, param, scale


@param("spread_quality", "liquidity")
def spread_quality(data: ParamInput):
    worst = max(leg.spread / leg.mid * 100 for leg in data.candidate.legs)
    return worst, 100 - scale(worst, 0, 10)


@param("leg_volume", "liquidity")
def leg_volume(data: ParamInput):
    lowest = min(leg.volume for leg in data.candidate.legs)
    return lowest, scale(math.log10(max(lowest, 1)), 2, 6)


@param("leg_oi", "liquidity")
def leg_oi(data: ParamInput):
    lowest = min(leg.oi for leg in data.candidate.legs)
    return lowest, scale(math.log10(max(lowest, 1)), 3, 7)


@param("strike_distance", "liquidity")
def strike_distance(data: ParamInput):
    farthest = max(abs(leg.strike / leg.forward - 1) * 100 for leg in data.candidate.legs)
    return farthest, 100 - scale(farthest, 0, 8)

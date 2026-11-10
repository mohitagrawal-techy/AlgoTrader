"""Implied volatility parameters."""

from __future__ import annotations

from algoverse.evaluation.params.base import ParamInput, param, scale


def _note(data: ParamInput) -> str:
    ranking = data.ctx.iv.ranking
    return f"source: {data.ctx.iv.source}, sample: {ranking.sample_size} days"


@param("iv_rank", "volatility")
def iv_rank(data: ParamInput):
    ranking = data.ctx.iv.ranking
    return None if ranking is None else (ranking.rank, ranking.rank, _note(data))


@param("iv_percentile", "volatility")
def iv_percentile(data: ParamInput):
    ranking = data.ctx.iv.ranking
    return None if ranking is None else (ranking.percentile, ranking.percentile, _note(data))


@param("iv_rv_spread", "volatility")
def iv_rv_spread(data: ParamInput):
    iv, realised = data.stats.atm_iv, data.ctx.realised_vol
    if iv is None or realised is None:
        return None
    points = (iv - realised) * 100
    return points, scale(points, -8, 8)


@param("term_slope", "volatility")
def term_slope(data: ParamInput):
    slope = data.ctx.term_slope
    if slope is None:
        return None
    points = slope * 100
    return points, scale(points, -4, 4)


@param("skew_25d", "volatility")
def skew_25d(data: ParamInput):
    skew = data.stats.skew
    if skew is None:
        return None
    points = skew * 100
    return points, scale(points, 0, 10)

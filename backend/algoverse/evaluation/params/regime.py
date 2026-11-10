"""Market regime parameters."""

from __future__ import annotations

from algoverse.evaluation.params.base import ParamInput, param, scale


@param("trend_alignment", "regime")
def trend_alignment(data: ParamInput):
    regime = data.ctx.regime
    if regime is None:
        return None
    trend, bias = regime.trend, data.bias
    if bias == "neutral":
        score = 100.0 if trend == "range" else 30.0
    elif trend == "range":
        score = 50.0
    else:
        aligned = (trend == "up") == (bias == "bullish")
        score = 100.0 if aligned else 0.0
    return score, score, f"trend is {trend}, structure is {bias}"


@param("trend_strength", "regime")
def trend_strength(data: ParamInput):
    regime = data.ctx.regime
    return None if regime is None else (regime.adx, scale(regime.adx, 10, 40))


@param("vix_level", "regime")
def vix_level(data: ParamInput):
    ranking = data.ctx.vix_ranking
    return None if ranking is None else (ranking.percentile, ranking.percentile)


@param("vix_change", "regime")
def vix_change(data: ParamInput):
    change = data.ctx.vix_change_pct
    return None if change is None else (change, scale(change, -15, 15))


@param("rv_percentile", "regime")
def rv_percentile(data: ParamInput):
    ranking = data.ctx.rv_ranking
    return None if ranking is None else (ranking.percentile, ranking.percentile)


@param("session_calm", "regime")
def session_calm(data: ParamInput):
    regime = data.ctx.regime
    if regime is None:
        return None
    activity = abs(regime.gap_pct) + regime.range_pct
    return activity, 100 - scale(activity, 0, 3)

"""Payoff-shape parameters."""

from __future__ import annotations

from algoverse.evaluation.params.base import ParamInput, param, scale


@param("pop", "structure")
def pop(data: ParamInput):
    value = data.structure.pop * 100
    return value, value


@param("ev_ratio", "structure")
def ev_ratio(data: ParamInput):
    """Modelled expected value after costs, per rupee at risk."""
    structure = data.structure
    if structure.max_loss == 0:
        return None
    ratio = structure.net_ev / structure.max_loss
    return ratio, scale(ratio, -0.2, 0.2)


@param("reward_risk", "structure")
def reward_risk(data: ParamInput):
    value = data.structure.reward_risk
    return None if value is None else (value, scale(value, 0, 3))


@param("breakeven_distance", "structure")
def breakeven_distance(data: ParamInput):
    """Nearest breakeven in expected moves. Sellers prefer high, buyers prefer low."""
    value = data.structure.breakeven_distance
    return None if value is None else (value, scale(value, 0, 2))


@param("theta_gamma", "structure")
def theta_gamma(data: ParamInput):
    value = data.structure.theta_gamma
    return None if value is None else (value, scale(value, 0, 2))


@param("delta_neutrality", "structure")
def delta_neutrality(data: ParamInput):
    structure = data.structure
    units = max(leg.units for leg in data.candidate.legs)
    imbalance = abs(structure.net_delta) / units
    return imbalance, 100 - scale(imbalance, 0, 0.5)


@param("return_on_margin", "structure")
def return_on_margin(data: ParamInput):
    structure = data.structure
    if structure.margin <= 0:
        return None
    value = structure.reward / structure.margin
    return value, scale(value, 0, 0.5)

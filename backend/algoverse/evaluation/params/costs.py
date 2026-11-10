"""Cost parameters."""

from __future__ import annotations

from algoverse.evaluation.params.base import ParamInput, param, scale


@param("cost_efficiency", "costs")
def cost_efficiency(data: ParamInput):
    ratio = data.structure.cost_ratio
    return None if ratio is None else (ratio, 100 - scale(ratio, 0, 0.5))

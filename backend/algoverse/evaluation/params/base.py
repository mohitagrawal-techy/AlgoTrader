"""Parameter registry. A parameter returns a raw value and a 0-100 level score."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from algoverse.evaluation.context import ExpiryStats, MarketContext
from algoverse.evaluation.structure import StructureMetrics
from algoverse.strategies.base import Candidate


@dataclass(frozen=True)
class ParamInput:
    candidate: Candidate
    structure: StructureMetrics
    ctx: MarketContext

    @property
    def stats(self) -> ExpiryStats:
        return self.ctx.by_expiry[self.candidate.front_expiry]

    @property
    def bias(self) -> str:
        return self.candidate.bias


@dataclass(frozen=True)
class ParamResult:
    name: str
    group: str
    raw: float | None
    score: float | None
    note: str = ""


ParamFunction = Callable[[ParamInput], tuple | None]
REGISTRY: dict[str, tuple[str, ParamFunction]] = {}


def param(name: str, group: str) -> Callable[[ParamFunction], ParamFunction]:
    def register(function: ParamFunction) -> ParamFunction:
        if name in REGISTRY:
            raise ValueError(f"Duplicate parameter: {name}")
        REGISTRY[name] = (group, function)
        return function

    return register


def evaluate_param(name: str, data: ParamInput) -> ParamResult:
    """Evaluate one parameter. A None return means the data is unavailable."""
    group, function = REGISTRY[name]
    output = function(data)
    if output is None:
        return ParamResult(name, group, None, None, "unavailable")
    raw, score, *note = output
    return ParamResult(name, group, float(raw), float(score), note[0] if note else "")


def scale(value: float, low: float, high: float) -> float:
    """Map `value` linearly so `low` is 0 and `high` is 100, clipped to that range."""
    return float(np.clip((value - low) / (high - low) * 100, 0, 100))


def by_bias(score: float, bias: str) -> float:
    """Turn a bullishness score into alignment with a bias. Neutral rewards balance."""
    if bias == "bullish":
        return score
    if bias == "bearish":
        return 100 - score
    return 100 - 2 * abs(score - 50)

"""Weighted scoring against a strategy's profile."""

from __future__ import annotations

from dataclasses import dataclass

import algoverse.evaluation.params  # noqa: F401  (registers parameters)
from algoverse.evaluation.config import Weight
from algoverse.evaluation.params.base import REGISTRY, ParamInput, ParamResult, evaluate_param


@dataclass(frozen=True)
class WeightedResult:
    result: ParamResult
    weight: float
    prefer: str
    adjusted: float | None


@dataclass(frozen=True)
class ScoreCard:
    composite: float | None
    coverage: float
    parts: tuple[WeightedResult, ...]


def apply_preference(score: float, prefer: str) -> float:
    if prefer == "low":
        return 100 - score
    if prefer == "mid":
        return 100 - 2 * abs(score - 50)
    return score


class Scorer:
    def __init__(self, profiles: dict[str, dict[str, Weight]]):
        unknown = {name for p in profiles.values() for name in p} - set(REGISTRY)
        if unknown:
            raise ValueError(f"Unknown parameters in scoring profiles: {sorted(unknown)}")
        self.profiles = profiles

    def score(self, data: ParamInput) -> ScoreCard:
        """Average over parameters with data. Coverage reports the share of weight available."""
        parts = []
        for name, weight in self.profiles[data.candidate.profile].items():
            result = evaluate_param(name, data)
            adjusted = (
                None if result.score is None else apply_preference(result.score, weight.prefer)
            )
            parts.append(WeightedResult(result, weight.w, weight.prefer, adjusted))
        total = sum(part.weight for part in parts)
        available = [part for part in parts if part.adjusted is not None]
        used = sum(part.weight for part in available)
        composite = sum(part.weight * part.adjusted for part in available) / used if used else None
        return ScoreCard(composite, used / total if total else 0.0, tuple(parts))

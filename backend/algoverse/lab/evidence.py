"""Statistics that separate a real edge from a lucky run.

Resampling is seeded, so the same results always give the same verdict.
"""

from __future__ import annotations

import math

import numpy as np

SAMPLES = 4000
SEED = 7
DAILY_BLOCK = 5
FLOOR_ALPHA = 0.10
MIN_POINTS = 2


def _resampled_means(values: np.ndarray, block: int) -> np.ndarray:
    """Means of resampled series. Blocks keep neighbouring days together."""
    size = len(values)
    block = max(1, min(block, size))
    count = math.ceil(size / block)
    starts = np.random.default_rng(SEED).integers(0, size, size=(SAMPLES, count))
    positions = (starts[..., None] + np.arange(block)) % size
    return values[positions.reshape(SAMPLES, -1)[:, :size]].mean(axis=1)


def luck_adjusted_floor(pnls: list[float], trials: int, alpha: float = FLOOR_ALPHA) -> dict | None:
    """A cautious lower bound on profit per trade, stricter the more strategies were tried."""
    if len(pnls) < MIN_POINTS:
        return None
    means = _resampled_means(np.asarray(pnls, dtype=float), 1)
    tail = max(alpha / max(trials, 1), 1 / SAMPLES)
    return {
        "floor": float(np.quantile(means, tail)),
        "prob_positive": float((means > 0).mean()),
        "trials": max(trials, 1),
    }


def prob_better(child: dict[str, float], parent: dict[str, float]) -> float:
    """Chance that the child truly earns more per day than its parent, on the same days."""
    days = sorted(set(child) | set(parent))
    if len(days) < MIN_POINTS:
        return 0.0
    difference = np.array([child.get(day, 0.0) - parent.get(day, 0.0) for day in days])
    if not np.any(difference):
        return 0.0
    return float((_resampled_means(difference, DAILY_BLOCK) > 0).mean())

"""Instant repricing of a position across spot gaps and IV shocks."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from algoverse.analytics.pricing import black76_price
from algoverse.risk.config import StressConfig
from algoverse.strategies.base import Candidate


@dataclass(frozen=True)
class StressResult:
    worst_pnl: float
    spot_move_pct: float
    iv_shift_pct: float


def stress_test(candidate: Candidate, config: StressConfig, r: float) -> StressResult:
    """Return the worst one-lot P&L, with no time passing."""
    moves = np.asarray(config.spot_moves_pct, float)
    shifts = np.asarray(config.iv_shifts_pct, float)
    move_grid, shift_grid = np.meshgrid(moves, shifts, indexing="ij")
    pnl = np.zeros_like(move_grid)
    for leg in candidate.legs:
        value = black76_price(
            leg.forward * (1 + move_grid / 100),
            leg.strike,
            leg.T,
            leg.iv * (1 + shift_grid / 100),
            r,
            leg.is_call,
        )
        pnl += leg.side * leg.units * (value - leg.entry_price)
    row, column = np.unravel_index(np.argmin(pnl), pnl.shape)
    return StressResult(float(pnl[row, column]), float(moves[row]), float(shifts[column]))

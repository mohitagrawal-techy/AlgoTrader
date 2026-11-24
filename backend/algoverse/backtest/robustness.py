"""Checks whether a chosen setting is stable or a lucky single peak."""

from __future__ import annotations

import numpy as np

from algoverse.backtest.engine import ReplayResult
from algoverse.backtest.report import daily_stats

NEIGHBOUR_SHARE = 0.5


def peak_check(results: list[ReplayResult], best: ReplayResult, capital: float) -> dict:
    """Compare the winner with settings one step away on the score threshold."""
    peers = [r for r in results if r.spec.disabled_profiles == best.spec.disabled_profiles]
    peers.sort(key=lambda r: r.spec.min_score)
    position = next(i for i, r in enumerate(peers) if r.spec == best.spec)
    neighbours = [peers[i] for i in (position - 1, position + 1) if 0 <= i < len(peers)]
    sharpes = [daily_stats(r.daily_pnl, capital).get("sharpe") for r in neighbours]
    known = [value if value is not None else 0.0 for value in sharpes]
    best_sharpe = daily_stats(best.daily_pnl, capital).get("sharpe") or 0.0
    average = float(np.mean(known)) if known else None
    return {
        "best_sharpe": best_sharpe,
        "neighbour_sharpe": average,
        "sharp_peak": average is not None and average < best_sharpe * NEIGHBOUR_SHARE,
    }

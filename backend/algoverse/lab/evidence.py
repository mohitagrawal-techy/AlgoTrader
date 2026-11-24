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


# ---- Sharpe ratios that account for how many strategies were tried -------------------------
#
# Bailey and López de Prado: the probabilistic Sharpe ratio is the chance the true Sharpe is
# above a benchmark, given the sample's length, skew and fat tails. The deflated Sharpe ratio
# uses as benchmark the Sharpe the best of N unskilled trials would show by luck alone.

EULER_GAMMA = 0.5772156649015329
TRADING_DAYS = 252


def _normal_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


def _normal_ppf(p: float) -> float:
    """Inverse of the standard normal CDF, by bisection: accurate and dependency-free."""
    low, high = -12.0, 12.0
    for _ in range(100):
        mid = (low + high) / 2
        if _normal_cdf(mid) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def probabilistic_sharpe(returns: list[float], benchmark: float = 0.0) -> float | None:
    """Chance the true per-session Sharpe exceeds `benchmark` (also per session)."""
    values = np.asarray(returns, dtype=float)
    if len(values) < 3:
        return None
    deviation = values.std(ddof=1)
    if deviation == 0:
        return None
    sharpe = values.mean() / deviation
    centred = values - values.mean()
    moment2 = (centred**2).mean()
    skew = (centred**3).mean() / moment2**1.5
    kurtosis = (centred**4).mean() / moment2**2
    spread = 1 - skew * sharpe + (kurtosis - 1) / 4 * sharpe**2
    if spread <= 0:
        return None
    return _normal_cdf((sharpe - benchmark) * math.sqrt(len(values) - 1) / math.sqrt(spread))


def expected_max_sharpe(trials: int, variance: float) -> float:
    """The per-session Sharpe the best of `trials` unskilled strategies would show by luck."""
    if trials < 2 or variance <= 0:
        return 0.0
    return math.sqrt(variance) * (
        (1 - EULER_GAMMA) * _normal_ppf(1 - 1 / trials)
        + EULER_GAMMA * _normal_ppf(1 - 1 / (trials * math.e))
    )


def trial_spread(annual_sharpes: list[float]) -> float | None:
    """Variance of the per-session Sharpe across logged trials, from their annual figures."""
    values = [s for s in annual_sharpes if s is not None and math.isfinite(s)]
    if len(values) < 2:
        return None
    return float(np.var(np.asarray(values) / math.sqrt(TRADING_DAYS), ddof=1))


def significance(daily_pnl: list[float], capital: float, trials: int, variance: float | None):
    """Sharpe of the sessions, its probabilistic and deflated versions, and the luck benchmark.

    Returns None when there are too few sessions or no variation to measure."""
    if capital <= 0:
        return None
    returns = [pnl / capital for pnl in daily_pnl]
    plain = probabilistic_sharpe(returns, 0.0)
    if plain is None:
        return None
    values = np.asarray(returns)
    per_session = float(values.mean() / values.std(ddof=1))
    benchmark = expected_max_sharpe(trials, variance or 0.0)
    # Without a spread of trial Sharpes to measure luck by, the benchmark is zero and the
    # deflated ratio equals the probabilistic one.
    deflated = probabilistic_sharpe(returns, benchmark)
    return {
        "sharpe": per_session * math.sqrt(TRADING_DAYS),
        "psr": plain,
        "dsr": deflated,
        "luck_sharpe": benchmark * math.sqrt(TRADING_DAYS),
        "trials": trials,
        "sessions": len(returns),
    }

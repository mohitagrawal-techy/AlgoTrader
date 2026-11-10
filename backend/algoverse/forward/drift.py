"""Compares forward results with what the backtest predicted."""

from __future__ import annotations

import math

import pandas as pd

from algoverse.forward.gates import GatesConfig

IN = "in"
OUT = "out"
INSUFFICIENT = "insufficient_data"
NO_BASELINE = "no_baseline"


def extract_baseline(report: dict | None) -> dict | None:
    """Keep the figures a run needs from a walk-forward report."""
    if report is None:
        return None
    summary = report["summary"]
    overall = summary["overall"]
    if not overall.get("trades"):
        return None
    carlo = summary.get("monte_carlo") or {}
    return {
        "trades": overall["trades"],
        "win_rate": overall["win_rate"],
        "expectancy": overall["expectancy"],
        "pnl_std": overall.get("pnl_std"),
        "drawdown_p95": carlo.get("drawdown_p95"),
    }


def _check(key: str, label: str, status: str, actual, low=None, high=None, unit="") -> dict:
    return {
        "key": key,
        "label": label,
        "status": status,
        "actual": actual,
        "low": low,
        "high": high,
        "unit": unit,
    }


def drift_checks(
    baseline: dict | None,
    trades: pd.DataFrame,
    drawdown: float,
    slippage_ratio: float | None,
    fills: int,
    config: GatesConfig,
) -> list[dict]:
    count = len(trades)
    sigmas = config.confidence_sigmas
    pnl = trades["net_pnl"].astype(float) if count else pd.Series(dtype=float)
    win_rate = float((pnl > 0).mean()) if count else None
    expectancy = float(pnl.mean()) if count else None
    enough = count >= config.min_drift_samples

    def range_status(actual, low, high) -> str:
        if baseline is None:
            return NO_BASELINE
        if not enough:
            return INSUFFICIENT
        return IN if low <= actual <= high else OUT

    win_low = win_high = expect_low = expect_high = None
    if baseline is not None and count:
        p = baseline["win_rate"]
        margin = sigmas * math.sqrt(p * (1 - p) / count)
        win_low, win_high = max(p - margin, 0.0), min(p + margin, 1.0)
        spread = baseline.get("pnl_std")
        if spread is None and count > 1:
            spread = float(pnl.std(ddof=1))
        margin = sigmas * (spread or 0.0) / math.sqrt(count)
        expect_low = baseline["expectancy"] - margin
        expect_high = baseline["expectancy"] + margin

    limit = baseline.get("drawdown_p95") if baseline else None
    if baseline is None or limit is None:
        drawdown_status = NO_BASELINE
    else:
        drawdown_status = IN if drawdown <= limit else OUT

    if fills < config.min_drift_samples or slippage_ratio is None:
        slippage_status = INSUFFICIENT
    else:
        slippage_status = IN if slippage_ratio <= config.max_slippage_ratio else OUT

    return [
        _check(
            "win_rate",
            "Win rate",
            range_status(win_rate, win_low, win_high),
            win_rate,
            win_low,
            win_high,
            "fraction",
        ),
        _check(
            "expectancy",
            "Expectancy per trade",
            range_status(expectancy, expect_low, expect_high),
            expectancy,
            expect_low,
            expect_high,
            "money",
        ),
        _check("drawdown", "Drawdown", drawdown_status, drawdown, None, limit, "loss"),
        _check(
            "slippage",
            "Observed vs modelled spread",
            slippage_status,
            slippage_ratio,
            None,
            config.max_slippage_ratio,
            "ratio",
        ),
    ]

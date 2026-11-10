"""Performance statistics and report assembly."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import numpy as np
import pandas as pd

from algoverse.backtest.config import BacktestConfig

TRADING_DAYS = 252
DTE_BUCKETS = (
    (0, 3, "0-3 days"),
    (4, 7, "4-7 days"),
    (8, 14, "8-14 days"),
    (15, 10_000, "15+ days"),
)
BREAKDOWNS = ("strategy", "symbol", "dte_bucket", "iv_regime", "trend")


def _longest_losing_streak(pnl: np.ndarray) -> int:
    longest = current = 0
    for value in pnl:
        current = current + 1 if value <= 0 else 0
        longest = max(longest, current)
    return longest


def max_drawdown(cumulative: np.ndarray) -> float:
    """Largest fall from a prior peak, in rupees, measured from a starting level of zero."""
    if len(cumulative) == 0:
        return 0.0
    peaks = np.maximum.accumulate(np.concatenate([[0.0], cumulative]))
    return float((peaks - np.concatenate([[0.0], cumulative])).max())


def trade_stats(trades: pd.DataFrame, tail_share: float) -> dict:
    if trades.empty:
        return {"trades": 0}
    ordered = trades.sort_values("closed")
    pnl = ordered["net_pnl"].to_numpy(float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gross_loss = -losses.sum()
    tail_count = max(1, int(np.ceil(len(pnl) * tail_share)))
    return {
        "trades": len(pnl),
        "net_pnl": float(pnl.sum()),
        "costs": float(ordered["costs"].sum()),
        "win_rate": float(len(wins) / len(pnl)),
        "average_win": float(wins.mean()) if len(wins) else 0.0,
        "average_loss": float(losses.mean()) if len(losses) else 0.0,
        "expectancy": float(pnl.mean()),
        "pnl_std": float(pnl.std(ddof=1)) if len(pnl) > 1 else None,
        "profit_factor": None if gross_loss == 0 else float(wins.sum() / gross_loss),
        "longest_losing_streak": _longest_losing_streak(pnl),
        "average_holding_days": float(ordered["holding_days"].mean()),
        "tail_loss": float(np.sort(pnl)[:tail_count].mean()),
    }


def daily_stats(daily: pd.Series, capital: float) -> dict:
    if daily.empty:
        return {}
    values = daily.to_numpy(float)
    returns = values / capital
    deviation = returns.std(ddof=1) if len(returns) > 1 else 0.0
    negative = returns[returns < 0]
    downside = np.sqrt((negative**2).sum() / len(returns))
    years = len(returns) / TRADING_DAYS
    months = pd.Series(values, index=pd.to_datetime(list(daily.index))).resample("ME").sum()
    drawdown = max_drawdown(values.cumsum())
    return {
        "sessions": len(values),
        "annual_return": float(returns.sum() / years) if years else None,
        "annual_volatility": float(deviation * np.sqrt(TRADING_DAYS)),
        "sharpe": None
        if deviation == 0
        else float(returns.mean() / deviation * np.sqrt(TRADING_DAYS)),
        "sortino": None
        if downside == 0
        else float(returns.mean() / downside * np.sqrt(TRADING_DAYS)),
        "max_drawdown": drawdown,
        "max_drawdown_pct": drawdown / capital * 100,
        "worst_day": float(values.min()),
        "negative_months_pct": float((months < 0).mean() * 100),
    }


def _dte_bucket(dte: int) -> str:
    return next(label for low, high, label in DTE_BUCKETS if low <= dte <= high)


def breakdowns(trades: pd.DataFrame, tail_share: float) -> dict[str, list[dict]]:
    if trades.empty:
        return {name: [] for name in BREAKDOWNS}
    labelled = trades.assign(dte_bucket=trades["dte_at_entry"].map(_dte_bucket))
    return {
        name: sorted(
            (
                {"group": str(key)} | trade_stats(group, tail_share)
                for key, group in labelled.groupby(name)
            ),
            key=lambda row: -row["net_pnl"],
        )
        for name in BREAKDOWNS
    }


def monte_carlo(pnl: np.ndarray, runs: int, capital: float, seed: int = 11) -> dict | None:
    """Drawdown range from reshuffling the order of trades."""
    if len(pnl) < 2:
        return None
    rng = np.random.default_rng(seed)
    shuffled = rng.permuted(np.tile(pnl, (runs, 1)), axis=1)
    cumulative = np.hstack([np.zeros((runs, 1)), shuffled.cumsum(axis=1)])
    drawdowns = (np.maximum.accumulate(cumulative, axis=1) - cumulative).max(axis=1)
    p5, p50, p95 = np.percentile(drawdowns, [5, 50, 95])
    return {
        "runs": runs,
        "drawdown_p5": float(p5),
        "drawdown_p50": float(p50),
        "drawdown_p95": float(p95),
        "drawdown_p95_pct": float(p95 / capital * 100),
    }


def stress_windows(
    trades: pd.DataFrame, daily: pd.Series, config: BacktestConfig, capital: float
) -> list[dict]:
    rows = []
    covered = list(daily.index)
    for window in config.stress_windows:
        days = [d for d in covered if window.start <= d <= window.end]
        if not days:
            rows.append({"name": window.name, "covered": False})
            continue
        in_window = (
            trades[(trades["closed"] >= window.start) & (trades["closed"] <= window.end)]
            if not trades.empty
            else trades
        )
        rows.append(
            {
                "name": window.name,
                "covered": True,
                "net_pnl": float(daily.loc[days].sum()),
                "max_drawdown": max_drawdown(daily.loc[days].to_numpy(float).cumsum()),
                "trades": len(in_window),
            }
        )
    return rows


def warnings_for(
    trades: pd.DataFrame, config: BacktestConfig, windows: Sequence[dict]
) -> list[str]:
    notes = []
    if len(trades) < config.min_trades_for_confidence:
        notes.append(
            f"Only {len(trades)} trades. At least {config.min_trades_for_confidence} are needed "
            "before these statistics mean much."
        )
    if not trades.empty and trades["iv_regime"].nunique() < 2:
        notes.append("Trades cover only one volatility regime.")
    if windows and not any(window["covered"] for window in windows):
        notes.append("The data does not cover any configured stress period.")
    return notes


def summarize(
    trades: pd.DataFrame, daily: pd.Series, config: BacktestConfig, capital: float
) -> dict:
    windows = stress_windows(trades, daily, config, capital)
    pnl = trades.sort_values("closed")["net_pnl"].to_numpy(float) if not trades.empty else None
    short = trades[trades["profile"] == "short_premium"] if not trades.empty else trades
    return {
        "overall": trade_stats(trades, config.tail_share) | daily_stats(daily, capital),
        "short_premium_tail_loss": trade_stats(short, config.tail_share).get("tail_loss"),
        "breakdowns": breakdowns(trades, config.tail_share),
        "monte_carlo": None if pnl is None else monte_carlo(pnl, config.monte_carlo_runs, capital),
        "stress_windows": windows,
        "warnings": warnings_for(trades, config, windows),
        "equity": [
            {"day": d, "pnl": float(p)}
            for d, p in zip(daily.index, daily.cumsum().to_numpy(float), strict=True)
        ],
    }


def date_range(sessions: Sequence[date]) -> dict:
    return (
        {"start": sessions[0], "end": sessions[-1], "sessions": len(sessions)} if sessions else {}
    )

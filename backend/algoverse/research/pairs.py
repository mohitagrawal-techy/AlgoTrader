"""Pair study between two indices: is their price spread mean-reverting, and would a simple
z-score rule on it have paid after costs?

This works on index levels, which cannot be traded directly. A real pair trade needs futures,
and futures history starts on 28 Oct 2026, too short to test. So this is a screen for whether
building futures or cross-index legs into the lab is worth it, not a backtest of a strategy.

Everything in the walk-forward test uses only past sessions: the hedge ratio comes from the
previous `lookback` sessions, the z-score from the previous `z_window`, and a signal at one
close is traded from that close to the next.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252
# Engle-Granger critical values for two series, from MacKinnon (2010).
EG_CRITICAL = {"1%": -3.90, "5%": -3.34, "10%": -3.04}


def _ols(y: np.ndarray, x: np.ndarray) -> tuple[float, float]:
    """Intercept and slope of y on x."""
    slope, intercept = np.polyfit(x, y, 1)
    return float(intercept), float(slope)


def adf_t(series: np.ndarray, lags: int = 1) -> float | None:
    """Augmented Dickey-Fuller t-statistic of a residual series, without a constant."""
    values = np.asarray(series, dtype=float)
    if len(values) < 30:
        return None
    diff = np.diff(values)
    rows = len(diff) - lags
    if rows < 20:
        return None
    y = diff[lags:]
    columns = [values[lags:-1]] + [diff[lags - k : -k] for k in range(1, lags + 1)]
    x = np.column_stack(columns)
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    residual = y - x @ coef
    dof = rows - x.shape[1]
    sigma2 = residual @ residual / dof
    cov = sigma2 * np.linalg.inv(x.T @ x)
    error = math.sqrt(cov[0, 0])
    return float(coef[0] / error) if error > 0 else None


def half_life(spread: np.ndarray) -> float | None:
    """Sessions for a gap from the spread's mean to halve, from an AR(1) fit."""
    values = np.asarray(spread, dtype=float)
    if len(values) < 30:
        return None
    lagged = values[:-1] - values[:-1].mean()
    change = np.diff(values)
    slope = float(lagged @ change / (lagged @ lagged))
    if slope >= 0:
        return None
    return float(-math.log(2) / math.log(1 + slope)) if slope > -1 else 0.0


def _stats(returns: pd.Series, positions: pd.Series) -> dict:
    values = returns.to_numpy(float)
    if len(values) == 0:
        return {"sessions": 0}
    equity = np.cumsum(values)
    drawdown = float(np.max(np.maximum.accumulate(equity) - equity)) if len(equity) else 0.0
    deviation = values.std(ddof=1) if len(values) > 1 else 0.0
    changes = positions.diff().fillna(positions.iloc[0] if len(positions) else 0)
    entries = int(((changes != 0) & (positions != 0)).sum())
    # P&L of each holding period, for the share that made money.
    trade_id = (
        ((positions != positions.shift()) & (positions != 0))
        .cumsum()
        .where(positions.shift().fillna(0) != 0)
    )
    per_trade = returns.groupby(trade_id).sum() if trade_id.notna().any() else pd.Series()
    return {
        "sessions": len(values),
        "trades": entries,
        "total_return_pct": float(equity[-1] * 100),
        "sharpe": None
        if deviation == 0
        else float(values.mean() / deviation * math.sqrt(TRADING_DAYS)),
        "max_drawdown_pct": drawdown * 100,
        "win_share": float((per_trade > 0).mean()) if len(per_trade) else None,
        "time_in_market": float((positions != 0).mean()),
    }


def pair_study(
    a: pd.Series,
    b: pd.Series,
    names: tuple[str, str],
    lookback: int = 250,
    z_window: int = 60,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
    cost_bps: float = 4.0,
    test_share: float = 0.3,
) -> dict:
    """Cointegration, half-life and a walk-forward z-score test of log(a) against log(b).

    `cost_bps` is charged on every change of position, per unit of gross exposure: brokerage,
    taxes and slippage on both futures legs together."""
    frame = pd.DataFrame({"a": np.log(a), "b": np.log(b)}).dropna()
    if len(frame) < lookback + z_window + 40:
        return {"names": names, "sessions": len(frame), "error": "Not enough shared history."}
    intercept, slope = _ols(frame["a"].to_numpy(), frame["b"].to_numpy())
    residual = frame["a"] - (intercept + slope * frame["b"])
    t_stat = adf_t(residual.to_numpy())
    recent = frame.iloc[-lookback:]
    recent_spread = (
        recent["a"] - _ols(recent["a"].to_numpy(), recent["b"].to_numpy())[1] * recent["b"]
    )

    # Walk-forward: hedge ratio from the previous `lookback` sessions, refit weekly.
    ratios = pd.Series(np.nan, index=frame.index)
    for end in range(lookback, len(frame), 5):
        window = frame.iloc[end - lookback : end]
        ratios.iloc[end] = _ols(window["a"].to_numpy(), window["b"].to_numpy())[1]
    ratios = ratios.ffill()
    spread = frame["a"] - ratios * frame["b"]
    mean = spread.rolling(z_window).mean()
    deviation = spread.rolling(z_window).std()
    z = (spread - mean) / deviation

    positions = np.zeros(len(frame))
    holding = 0
    for i, value in enumerate(z.to_numpy()):
        if np.isnan(value):
            holding = 0
        elif holding == 0 and abs(value) >= entry_z:
            holding = -1 if value > 0 else 1
        elif holding != 0 and abs(value) <= exit_z:
            holding = 0
        positions[i] = holding
    held = pd.Series(positions, index=frame.index)
    gross = 1 + ratios.abs()
    move = (frame["a"].diff() - ratios.shift(1) * frame["b"].diff()) / gross.shift(1)
    returns = (held.shift(1) * move).fillna(0.0)
    costs = held.diff().abs().fillna(0.0) * cost_bps / 10_000
    net = (returns - costs).iloc[lookback + z_window :]
    held = held.iloc[lookback + z_window :]
    cut = int(len(net) * (1 - test_share))
    return {
        "names": list(names),
        "sessions": len(frame),
        "start": str(pd.Timestamp(frame.index[0]).date()),
        "end": str(pd.Timestamp(frame.index[-1]).date()),
        "hedge_ratio": slope,
        "adf_t": t_stat,
        "cointegrated_at": next(
            (
                level
                for level, value in EG_CRITICAL.items()
                if t_stat is not None and t_stat < value
            ),
            None,
        ),
        "half_life_sessions": half_life(recent_spread.to_numpy()),
        "latest_z": None if np.isnan(z.iloc[-1]) else float(z.iloc[-1]),
        "rules": {
            "lookback": lookback,
            "z_window": z_window,
            "entry_z": entry_z,
            "exit_z": exit_z,
            "cost_bps": cost_bps,
        },
        "train": _stats(net.iloc[:cut], held.iloc[:cut])
        | {"start": str(pd.Timestamp(net.index[0]).date())},
        "test": _stats(net.iloc[cut:], held.iloc[cut:])
        | {"start": str(pd.Timestamp(net.index[cut]).date())},
        "equity": [
            {"day": str(pd.Timestamp(day).date()), "value": float(value * 100)}
            for day, value in net.cumsum().iloc[::5].items()
        ],
        "test_start": str(pd.Timestamp(net.index[cut]).date()),
    }

"""Trend and realised-volatility measures. Every series uses only current and past bars."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252
ADX_TREND_THRESHOLD = 25.0


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def realised_vol(close: pd.Series, window: int = 20) -> pd.Series:
    """Annualised close-to-close volatility, as a decimal."""
    returns = np.log(close / close.shift(1))
    return returns.rolling(window).std() * np.sqrt(TRADING_DAYS)


def adx(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's Average Directional Index."""
    high, low, close = bars["high"], bars["low"], bars["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    true_range = pd.concat(
        [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1
    ).max(axis=1, skipna=False)
    smooth = {"alpha": 1 / period, "adjust": False, "min_periods": period}
    atr = true_range.ewm(**smooth).mean()
    plus_di = 100 * plus_dm.ewm(**smooth).mean() / atr
    minus_di = 100 * minus_dm.ewm(**smooth).mean() / atr
    total = (plus_di + minus_di).replace(0.0, np.nan)
    return (100 * (plus_di - minus_di).abs() / total).ewm(**smooth).mean()


@dataclass(frozen=True)
class RegimeSnapshot:
    trend: str
    adx: float
    ema_fast: float
    ema_slow: float
    realised_vol: float
    gap_pct: float
    range_pct: float


def regime_snapshot(bars: pd.DataFrame) -> RegimeSnapshot | None:
    """Summarise the final bar, or None when history is too short."""
    close = bars["close"]
    values = {
        "ema_fast": ema(close, 20).iloc[-1],
        "ema_slow": ema(close, 50).iloc[-1],
        "adx": adx(bars).iloc[-1],
        "realised_vol": realised_vol(close).iloc[-1],
    }
    if len(bars) < 2 or any(pd.isna(v) for v in values.values()):
        return None
    trending = values["adx"] >= ADX_TREND_THRESHOLD
    if trending and values["ema_fast"] > values["ema_slow"]:
        trend = "up"
    elif trending and values["ema_fast"] < values["ema_slow"]:
        trend = "down"
    else:
        trend = "range"
    last, previous = bars.iloc[-1], bars.iloc[-2]
    return RegimeSnapshot(
        trend=trend,
        gap_pct=float((last["open"] / previous["close"] - 1) * 100),
        range_pct=float((last["high"] - last["low"]) / previous["close"] * 100),
        **{k: float(v) for k, v in values.items()},
    )

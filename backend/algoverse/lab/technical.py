"""Technical rules for strategy entries: indicators, candle patterns and price levels.

A rule reads the underlying's candles on its own timeframe. Daily candles run up to the
decision, including the session in progress, so a pattern that forms today can be acted on at
the next open. Weekly and monthly candles are built from the daily ones; the current week or
month counts as it stands at the decision.

Two kinds of rule:
    compare  left operand, an operator, right operand. Each operand is a number or an indicator,
             optionally read some candles ago. Operators: above, below, crosses_above,
             crosses_below.
    pattern  a candle pattern that appeared, or did not, within the last N candles.

Intraday timeframes (5m, 15m, 1h) need minute history and an intraday backtester; the timeframe
list is where they will be added.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, model_validator

from algoverse.analytics.regime import rsi

Timeframe = Literal["5m", "15m", "1h", "1d", "1w", "1mo"]
TIMEFRAMES: dict[str, str] = {
    "5m": "5-minute",
    "15m": "15-minute",
    "1h": "Hourly",
    "1d": "Daily",
    "1w": "Weekly",
    "1mo": "Monthly",
}
# Intraday candles start at the 09:15 open, so an hourly candle runs 09:15 to 10:15.
INTRADAY_MINUTES = {"5m": 5, "15m": 15, "1h": 60}
SESSION_START = pd.Timedelta(hours=9, minutes=15)
MAX_BARS_AGO = 50


# ---- Indicators ----------------------------------------------------------------------------


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def _wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _true_range(bars: pd.DataFrame) -> pd.Series:
    previous = bars["close"].shift(1)
    return pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - previous).abs(),
            (bars["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _atr(bars: pd.DataFrame, period: int) -> pd.Series:
    return _wilder(_true_range(bars), period)


def _macd(bars: pd.DataFrame, fast: int, slow: int, signal: int) -> dict[str, pd.Series]:
    line = _ema(bars["close"], fast) - _ema(bars["close"], slow)
    trigger = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return {"macd": line, "signal": trigger, "hist": line - trigger}


def _bbands(bars: pd.DataFrame, period: int, std: float) -> dict[str, pd.Series]:
    middle = bars["close"].rolling(period).mean()
    spread = bars["close"].rolling(period).std(ddof=0) * std
    upper, lower = middle + spread, middle - spread
    return {
        "upper": upper,
        "middle": middle,
        "lower": lower,
        "width_pct": (upper - lower) / middle * 100,
        "percent_b": (bars["close"] - lower) / (upper - lower).replace(0, np.nan),
    }


def _adx(bars: pd.DataFrame, period: int) -> dict[str, pd.Series]:
    up = bars["high"].diff()
    down = -bars["low"].diff()
    plus = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=bars.index)
    minus = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=bars.index)
    atr = _atr(bars, period).replace(0, np.nan)
    plus_di = 100 * _wilder(plus, period) / atr
    minus_di = 100 * _wilder(minus, period) / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return {"adx": _wilder(dx, period), "plus_di": plus_di, "minus_di": minus_di}


def _supertrend(bars: pd.DataFrame, period: int, multiplier: float) -> dict[str, pd.Series]:
    atr = _atr(bars, period).to_numpy()
    high, low, close = (bars[c].to_numpy(float) for c in ("high", "low", "close"))
    middle = (high + low) / 2
    upper_basic, lower_basic = middle + multiplier * atr, middle - multiplier * atr
    n = len(bars)
    upper, lower = np.full(n, np.nan), np.full(n, np.nan)
    line, direction = np.full(n, np.nan), np.full(n, np.nan)
    for i in range(n):
        if np.isnan(atr[i]):
            continue
        if i == 0 or np.isnan(upper[i - 1]):
            upper[i], lower[i] = upper_basic[i], lower_basic[i]
            direction[i] = 1.0 if close[i] >= middle[i] else -1.0
        else:
            upper[i] = (
                upper_basic[i]
                if upper_basic[i] < upper[i - 1] or close[i - 1] > upper[i - 1]
                else upper[i - 1]
            )
            lower[i] = (
                lower_basic[i]
                if lower_basic[i] > lower[i - 1] or close[i - 1] < lower[i - 1]
                else lower[i - 1]
            )
            if direction[i - 1] < 0:
                direction[i] = 1.0 if close[i] > upper[i - 1] else -1.0
            else:
                direction[i] = -1.0 if close[i] < lower[i - 1] else 1.0
        line[i] = lower[i] if direction[i] > 0 else upper[i]
    return {
        "line": pd.Series(line, index=bars.index),
        "direction": pd.Series(direction, index=bars.index),
    }


def _stochastic(bars: pd.DataFrame, k: int, d: int) -> dict[str, pd.Series]:
    low = bars["low"].rolling(k).min()
    high = bars["high"].rolling(k).max()
    fast = 100 * (bars["close"] - low) / (high - low).replace(0, np.nan)
    return {"k": fast, "d": fast.rolling(d).mean()}


def _session_offsets(bars: pd.DataFrame, minutes: int):
    """Each candle's session and minutes after 09:15, or None when the candles cannot hold a
    `minutes` opening range (daily or longer, or longer than the range)."""
    if bars.empty or "ts" not in bars:
        return None
    times = pd.to_datetime(bars["ts"])
    local = times.dt.tz_convert("Asia/Kolkata") if times.dt.tz is not None else times
    day = local.dt.normalize()
    offset = (local - day - SESSION_START).dt.total_seconds() / 60
    gaps = offset.groupby(day).diff().dropna()
    size = float(gaps[gaps > 0].min()) if (gaps > 0).any() else float("inf")
    if size > minutes or (offset.groupby(day).nunique() <= 1).all():
        return None
    return day, offset


def _opening_range(bars: pd.DataFrame, minutes: int) -> dict[str, pd.Series]:
    """The high and low of each session's first `minutes`, from 09:15.

    Intraday candles only. A candle gets the values once the range has finished, so the range
    is never read before it is known. Candles longer than the range, and daily or longer
    candles, have no value.
    """
    empty = pd.Series(np.nan, index=bars.index, dtype=float)
    found = _session_offsets(bars, minutes)
    if found is None:
        return {"high": empty, "low": empty, "mid": empty}
    day, offset = found
    inside = offset < minutes
    high = bars["high"].where(inside).groupby(day).transform("max")
    low = bars["low"].where(inside).groupby(day).transform("min")
    # Known only from the first candle that starts after the range ends.
    known = offset >= minutes
    high, low = high.where(known), low.where(known)
    return {"high": high, "low": low, "mid": (high + low) / 2}


def _opening_range_body(bars: pd.DataFrame, minutes: int) -> dict[str, pd.Series]:
    """The open and close of each session's first `minutes`: the open of its first candle and
    the close of its last. "high" is the higher of the two and "low" the lower, so wicks are
    left out. Known, like the high-low range, only once the range has finished."""
    empty = pd.Series(np.nan, index=bars.index, dtype=float)
    found = _session_offsets(bars, minutes)
    if found is None:
        return {"high": empty, "low": empty, "mid": empty, "open": empty, "close": empty}
    day, offset = found
    inside = offset < minutes
    first_open = bars["open"].where(inside).groupby(day).transform("first")
    last_close = bars["close"].where(inside).groupby(day).transform("last")
    known = offset >= minutes
    first_open, last_close = first_open.where(known), last_close.where(known)
    high = np.fmax(first_open, last_close).where(known)
    low = np.fmin(first_open, last_close).where(known)
    return {
        "high": high,
        "low": low,
        "mid": (high + low) / 2,
        "open": first_open,
        "close": last_close,
    }


def _pivots(bars: pd.DataFrame) -> dict[str, pd.Series]:
    """Classic floor pivots from the previous candle."""
    high, low, close = (bars[c].shift(1) for c in ("high", "low", "close"))
    pivot = (high + low + close) / 3
    return {
        "p": pivot,
        "r1": 2 * pivot - low,
        "s1": 2 * pivot - high,
        "r2": pivot + (high - low),
        "s2": pivot - (high - low),
    }


def _wma(series: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1, dtype=float)
    return series.rolling(period).apply(
        lambda v: float(np.dot(v, weights) / weights.sum()), raw=True
    )


def _cci(bars: pd.DataFrame, period: int) -> pd.Series:
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3
    mean = typical.rolling(period).mean()
    deviation = typical.rolling(period).apply(
        lambda v: float(np.abs(v - v.mean()).mean()), raw=True
    )
    return (typical - mean) / (0.015 * deviation.replace(0, np.nan))


def _williams_r(bars: pd.DataFrame, period: int) -> pd.Series:
    high = bars["high"].rolling(period).max()
    low = bars["low"].rolling(period).min()
    return -100 * (high - bars["close"]) / (high - low).replace(0, np.nan)


def _donchian(bars: pd.DataFrame, period: int) -> dict[str, pd.Series]:
    upper = bars["high"].rolling(period).max()
    lower = bars["low"].rolling(period).min()
    return {"upper": upper, "middle": (upper + lower) / 2, "lower": lower}


def heikin_ashi(bars: pd.DataFrame) -> pd.DataFrame:
    """Heikin-Ashi candles: smoothed bodies that make trends easier to read."""
    if bars.empty:
        return bars
    close = (bars["open"] + bars["high"] + bars["low"] + bars["close"]).to_numpy(float) / 4
    opens = np.empty(len(bars))
    opens[0] = (float(bars["open"].iloc[0]) + float(bars["close"].iloc[0])) / 2
    for i in range(1, len(bars)):
        opens[i] = (opens[i - 1] + close[i - 1]) / 2
    frame = bars.copy()
    frame["open"] = opens
    frame["close"] = close
    frame["high"] = np.maximum.reduce([bars["high"].to_numpy(float), opens, close])
    frame["low"] = np.minimum.reduce([bars["low"].to_numpy(float), opens, close])
    return frame.reset_index(drop=True)


@dataclass(frozen=True)
class Param:
    name: str
    label: str
    default: float
    low: float
    high: float
    whole: bool = True


@dataclass(frozen=True)
class Indicator:
    label: str
    compute: Callable[..., pd.Series | dict[str, pd.Series]]
    params: tuple[Param, ...] = ()
    fields: dict[str, str] = field(default_factory=dict)
    # What the value is measured in, so the editor can suggest sensible numbers.
    unit: Literal["price", "percent", "index", "ratio", "direction"] = "price"
    description: str = ""
    # Needs 5-minute to hourly candles.
    intraday_only: bool = False


PERIOD = Param("period", "Period", 14, 2, 400)


def _p(name: str, label: str, default: float, low: float, high: float, whole: bool = True) -> Param:
    return Param(name, label, default, low, high, whole)


INDICATORS: dict[str, Indicator] = {
    "close": Indicator("Close", lambda b: b["close"], description="Closing price"),
    "open": Indicator("Open", lambda b: b["open"], description="Opening price"),
    "high": Indicator("High", lambda b: b["high"], description="Candle high"),
    "low": Indicator("Low", lambda b: b["low"], description="Candle low"),
    "prev_close": Indicator("Previous close", lambda b: b["close"].shift(1)),
    "prev_high": Indicator("Previous high", lambda b: b["high"].shift(1)),
    "prev_low": Indicator("Previous low", lambda b: b["low"].shift(1)),
    "sma": Indicator(
        "Simple moving average",
        lambda b, period: b["close"].rolling(int(period)).mean(),
        (_p("period", "Period", 20, 2, 400),),
    ),
    "ema": Indicator(
        "Exponential moving average",
        lambda b, period: _ema(b["close"], int(period)),
        (_p("period", "Period", 20, 2, 400),),
    ),
    "wma": Indicator(
        "Weighted moving average",
        lambda b, period: _wma(b["close"], int(period)),
        (_p("period", "Period", 20, 2, 400),),
    ),
    "rsi": Indicator(
        "RSI",
        lambda b, period: rsi(b["close"], int(period)),
        (PERIOD,),
        unit="index",
        description="Wilder's relative strength index, 0 to 100",
    ),
    "macd": Indicator(
        "MACD",
        lambda b, fast, slow, signal: _macd(b, int(fast), int(slow), int(signal)),
        (
            _p("fast", "Fast", 12, 2, 100),
            _p("slow", "Slow", 26, 3, 200),
            _p("signal", "Signal", 9, 2, 50),
        ),
        {"macd": "MACD line", "signal": "Signal line", "hist": "Histogram"},
        unit="ratio",
    ),
    "bbands": Indicator(
        "Bollinger Bands",
        lambda b, period, std: _bbands(b, int(period), float(std)),
        (_p("period", "Period", 20, 2, 200), _p("std", "Std devs", 2, 0.5, 4, whole=False)),
        {
            "upper": "Upper band",
            "middle": "Middle band",
            "lower": "Lower band",
            "width_pct": "Band width, %",
            "percent_b": "%B (0 lower, 1 upper)",
        },
    ),
    "atr": Indicator(
        "ATR",
        lambda b, period: _atr(b, int(period)),
        (PERIOD,),
        description="Average true range, points",
    ),
    "atr_pct": Indicator(
        "ATR, % of close",
        lambda b, period: _atr(b, int(period)) / b["close"] * 100,
        (PERIOD,),
        unit="percent",
    ),
    "supertrend": Indicator(
        "Supertrend",
        lambda b, period, multiplier: _supertrend(b, int(period), float(multiplier)),
        (
            _p("period", "ATR period", 10, 2, 100),
            _p("multiplier", "Multiplier", 3, 0.5, 10, whole=False),
        ),
        {"line": "Supertrend line", "direction": "Direction (+1 up, -1 down)"},
    ),
    "adx": Indicator(
        "ADX / DI",
        lambda b, period: _adx(b, int(period)),
        (PERIOD,),
        {"adx": "ADX", "plus_di": "+DI", "minus_di": "-DI"},
        unit="index",
    ),
    "stochastic": Indicator(
        "Stochastic",
        lambda b, k, d: _stochastic(b, int(k), int(d)),
        (_p("k", "%K period", 14, 2, 100), _p("d", "%D smoothing", 3, 1, 20)),
        {"k": "%K", "d": "%D"},
        unit="index",
    ),
    "roc": Indicator(
        "Rate of change, %",
        lambda b, period: b["close"].pct_change(int(period)) * 100,
        (_p("period", "Period", 10, 1, 200),),
        unit="percent",
    ),
    "cci": Indicator(
        "CCI",
        lambda b, period: _cci(b, int(period)),
        (_p("period", "Period", 20, 2, 200),),
        unit="index",
        description="Commodity channel index; above +100 is strong, below -100 weak",
    ),
    "williams_r": Indicator(
        "Williams %R",
        lambda b, period: _williams_r(b, int(period)),
        (PERIOD,),
        unit="index",
        description="-100 to 0; above -20 overbought, below -80 oversold",
    ),
    "donchian": Indicator(
        "Donchian channel",
        lambda b, period: _donchian(b, int(period)),
        (_p("period", "Candles", 20, 2, 250),),
        {"upper": "Upper", "middle": "Middle", "lower": "Lower"},
        description="Highest high and lowest low of the last N candles, including this one",
    ),
    "prior_high": Indicator(
        "Highest high of the prior N candles",
        lambda b, period: b["high"].rolling(int(period)).max().shift(1),
        (_p("period", "Candles", 20, 1, 250),),
        description="Excludes the current candle, for breakouts",
    ),
    "prior_low": Indicator(
        "Lowest low of the prior N candles",
        lambda b, period: b["low"].rolling(int(period)).min().shift(1),
        (_p("period", "Candles", 20, 1, 250),),
        description="Excludes the current candle, for breakdowns",
    ),
    "range_pct": Indicator(
        "Range of the last N candles, %",
        lambda b, period: (
            (b["high"].rolling(int(period)).max() - b["low"].rolling(int(period)).min())
            / b["close"]
            * 100
        ),
        (_p("period", "Candles", 5, 1, 250),),
        unit="percent",
        description="Narrow ranges mean consolidation",
    ),
    "body_pct": Indicator(
        "Candle body, %",
        lambda b: (b["close"] - b["open"]).abs() / b["open"] * 100,
        unit="percent",
    ),
    "gap_pct": Indicator(
        "Opening gap, %",
        lambda b: (b["open"] / b["close"].shift(1) - 1) * 100,
        unit="percent",
    ),
    "change_pct": Indicator(
        "Change from previous close, %",
        lambda b: (b["close"] / b["close"].shift(1) - 1) * 100,
        unit="percent",
    ),
    "opening_range": Indicator(
        "Opening range",
        lambda b, minutes: _opening_range(b, int(minutes)),
        (_p("minutes", "First minutes", 15, 5, 120),),
        {"high": "High", "low": "Low", "mid": "Middle"},
        description="High and low of the session's first minutes. Use on 5- or 15-minute candles",
        intraday_only=True,
    ),
    "opening_range_body": Indicator(
        "Opening range, open to close",
        lambda b, minutes: _opening_range_body(b, int(minutes)),
        (_p("minutes", "First minutes", 15, 5, 120),),
        {
            "high": "Upper (higher of open and close)",
            "low": "Lower (lower of open and close)",
            "mid": "Middle",
            "open": "Open",
            "close": "Close",
        },
        description=(
            "The open and close of the session's first minutes, without the wicks. "
            "Use on 5- or 15-minute candles"
        ),
        intraday_only=True,
    ),
    "pivots": Indicator(
        "Pivot levels (previous candle)",
        _pivots,
        fields={"p": "Pivot", "r1": "R1", "s1": "S1", "r2": "R2", "s2": "S2"},
    ),
}


# On a chart, drawn over the candles because they are prices. Anything else gets its own pane.
CHART_OVERLAY = {
    "close",
    "open",
    "high",
    "low",
    "prev_close",
    "prev_high",
    "prev_low",
    "sma",
    "ema",
    "wma",
    "bbands",
    "supertrend",
    "donchian",
    "prior_high",
    "prior_low",
    "opening_range",
    "opening_range_body",
    "pivots",
}
# Fields of overlay indicators that are not prices.
CHART_SEPARATE_FIELDS = {
    ("bbands", "width_pct"),
    ("bbands", "percent_b"),
    ("supertrend", "direction"),
}


# ---- Candle patterns -----------------------------------------------------------------------


def _parts(b: pd.DataFrame):
    body = (b["close"] - b["open"]).abs()
    span = (b["high"] - b["low"]).replace(0, np.nan)
    upper = b["high"] - b[["open", "close"]].max(axis=1)
    lower = b[["open", "close"]].min(axis=1) - b["low"]
    green = b["close"] > b["open"]
    red = b["close"] < b["open"]
    return body, span, upper, lower, green, red


def _engulfing(b: pd.DataFrame, bullish: bool) -> pd.Series:
    _, _, _, _, green, red = _parts(b)
    o, c = b["open"], b["close"]
    if bullish:
        return red.shift(1, fill_value=False) & green & (c >= o.shift(1)) & (o <= c.shift(1))
    return green.shift(1, fill_value=False) & red & (o >= c.shift(1)) & (c <= o.shift(1))


def _star(b: pd.DataFrame, morning: bool) -> pd.Series:
    body, span, _, _, green, red = _parts(b)
    first_body = body.shift(2)
    first_mid = (b["open"].shift(2) + b["close"].shift(2)) / 2
    small_middle = body.shift(1) <= first_body * 0.5
    if morning:
        return red.shift(2, fill_value=False) & small_middle & green & (b["close"] > first_mid)
    return green.shift(2, fill_value=False) & small_middle & red & (b["close"] < first_mid)


def _three(b: pd.DataFrame, up: bool) -> pd.Series:
    _, _, _, _, green, red = _parts(b)
    colour = green if up else red
    rising = b["close"] > b["close"].shift(1) if up else b["close"] < b["close"].shift(1)
    run = colour & rising
    return run & run.shift(1, fill_value=False) & colour.shift(2, fill_value=False)


@dataclass(frozen=True)
class Pattern:
    label: str
    bias: Literal["bullish", "bearish", "neutral"]
    detect: Callable[[pd.DataFrame], pd.Series]
    description: str


def _shape(b, test):
    body, span, upper, lower, green, red = _parts(b)
    return test(body, span, upper, lower, green, red).fillna(False)


PATTERNS: dict[str, Pattern] = {
    "bullish_engulfing": Pattern(
        "Bullish engulfing",
        "bullish",
        lambda b: _engulfing(b, True),
        "A green body that covers the previous red body",
    ),
    "bearish_engulfing": Pattern(
        "Bearish engulfing",
        "bearish",
        lambda b: _engulfing(b, False),
        "A red body that covers the previous green body",
    ),
    "hammer": Pattern(
        "Hammer",
        "bullish",
        lambda b: _shape(
            b, lambda body, span, up, low, g, r: (low >= 2 * body) & (up <= body) & (body > 0)
        ),
        "Long lower shadow, small body near the top",
    ),
    "shooting_star": Pattern(
        "Shooting star",
        "bearish",
        lambda b: _shape(
            b, lambda body, span, up, low, g, r: (up >= 2 * body) & (low <= body) & (body > 0)
        ),
        "Long upper shadow, small body near the bottom",
    ),
    "doji": Pattern(
        "Doji",
        "neutral",
        lambda b: _shape(b, lambda body, span, up, low, g, r: body <= span * 0.1),
        "Open and close almost equal",
    ),
    "bullish_marubozu": Pattern(
        "Bullish marubozu",
        "bullish",
        lambda b: _shape(b, lambda body, span, up, low, g, r: g & (body >= span * 0.9)),
        "A strong green candle with hardly any shadows",
    ),
    "bearish_marubozu": Pattern(
        "Bearish marubozu",
        "bearish",
        lambda b: _shape(b, lambda body, span, up, low, g, r: r & (body >= span * 0.9)),
        "A strong red candle with hardly any shadows",
    ),
    "inside_bar": Pattern(
        "Inside bar",
        "neutral",
        lambda b: (b["high"] < b["high"].shift(1)) & (b["low"] > b["low"].shift(1)),
        "High and low inside the previous candle",
    ),
    "outside_bar": Pattern(
        "Outside bar",
        "neutral",
        lambda b: (b["high"] > b["high"].shift(1)) & (b["low"] < b["low"].shift(1)),
        "High and low beyond the previous candle",
    ),
    "morning_star": Pattern(
        "Morning star",
        "bullish",
        lambda b: _star(b, True),
        "Red, small, then green closing above the first candle's middle",
    ),
    "evening_star": Pattern(
        "Evening star",
        "bearish",
        lambda b: _star(b, False),
        "Green, small, then red closing below the first candle's middle",
    ),
    "three_white_soldiers": Pattern(
        "Three white soldiers",
        "bullish",
        lambda b: _three(b, True),
        "Three green candles, each closing higher",
    ),
    "three_black_crows": Pattern(
        "Three black crows",
        "bearish",
        lambda b: _three(b, False),
        "Three red candles, each closing lower",
    ),
    "gap_up": Pattern(
        "Gap up",
        "bullish",
        lambda b: b["low"] > b["high"].shift(1),
        "Whole candle above the previous high",
    ),
    "gap_down": Pattern(
        "Gap down",
        "bearish",
        lambda b: b["high"] < b["low"].shift(1),
        "Whole candle below the previous low",
    ),
    "higher_high_low": Pattern(
        "Higher high and higher low",
        "bullish",
        lambda b: (b["high"] > b["high"].shift(1)) & (b["low"] > b["low"].shift(1)),
        "Both ends above the previous candle",
    ),
    "lower_high_low": Pattern(
        "Lower high and lower low",
        "bearish",
        lambda b: (b["high"] < b["high"].shift(1)) & (b["low"] < b["low"].shift(1)),
        "Both ends below the previous candle",
    ),
}


# ---- Rules ---------------------------------------------------------------------------------


class Operand(BaseModel):
    """A number, or an indicator read `bars_ago` candles back (0 is the latest)."""

    kind: Literal["value", "indicator"] = "indicator"
    value: float | None = None
    name: str | None = None
    params: dict[str, float] = Field(default_factory=dict)
    field: str | None = None
    bars_ago: int = Field(default=0, ge=0, le=MAX_BARS_AGO)

    @model_validator(mode="after")
    def _check(self) -> Operand:
        if self.kind == "value":
            if self.value is None:
                raise ValueError("a number operand needs a value")
            return self
        indicator = INDICATORS.get(self.name or "")
        if indicator is None:
            raise ValueError(f"unknown indicator {self.name!r}")
        known = {p.name: p for p in indicator.params}
        unknown = set(self.params) - set(known)
        if unknown:
            raise ValueError(f"{self.name} has no parameter {sorted(unknown)[0]!r}")
        for name, param in known.items():
            value = self.params.get(name, param.default)
            if not param.low <= value <= param.high:
                raise ValueError(
                    f"{self.name} {name} must be between {param.low:g} and {param.high:g}"
                )
            if param.whole and value != int(value):
                raise ValueError(f"{self.name} {name} must be a whole number")
        if indicator.fields:
            if self.field not in indicator.fields:
                raise ValueError(f"{self.name} needs one of {sorted(indicator.fields)}")
        elif self.field is not None:
            raise ValueError(f"{self.name} has no fields")
        return self


CandleType = Literal["normal", "heikin_ashi"]
CANDLE_TYPES = {"normal": "Normal", "heikin_ashi": "Heikin-Ashi"}


class CompareRule(BaseModel):
    type: Literal["compare"] = "compare"
    timeframe: Timeframe = "1d"
    candles: CandleType = "normal"
    left: Operand
    op: Literal["above", "below", "crosses_above", "crosses_below"]
    right: Operand

    @model_validator(mode="after")
    def _not_two_numbers(self) -> CompareRule:
        if self.left.kind == "value" and self.right.kind == "value":
            raise ValueError("a rule needs at least one indicator")
        for operand in (self.left, self.right):
            indicator = INDICATORS.get(operand.name or "")
            if indicator and indicator.intraday_only and self.timeframe not in INTRADAY_MINUTES:
                raise ValueError(f"{indicator.label} needs 5-minute, 15-minute or hourly candles")
        return self


class PatternRule(BaseModel):
    type: Literal["pattern"] = "pattern"
    timeframe: Timeframe = "1d"
    candles: CandleType = "normal"
    pattern: str
    # The pattern completed on one of the last `within` candles.
    within: int = Field(default=1, ge=1, le=20)
    present: bool = True

    @model_validator(mode="after")
    def _known(self) -> PatternRule:
        if self.pattern not in PATTERNS:
            raise ValueError(f"unknown candle pattern {self.pattern!r}")
        return self


TechRule = Annotated[CompareRule | PatternRule, Field(discriminator="type")]


# ---- Evaluation ----------------------------------------------------------------------------


def needs_minutes(rules) -> bool:
    return any(rule.timeframe in INTRADAY_MINUTES for rule in rules)


IST_OFFSET_MS = 19_800_000
DAY_MS = 86_400_000
SESSION_START_MIN = 9 * 60 + 15
SESSION_END_MIN = 15 * 60 + 30


def _wall_minutes(ts: pd.Series) -> np.ndarray:
    """Minutes since 1970 on the Indian wall clock, from aware (any zone) or naive local times."""
    if ts.dtype.kind != "M" and not isinstance(ts.dtype, pd.DatetimeTZDtype):
        ts = pd.to_datetime(ts)
    values = ts.array.asi8  # Epoch in the column's own unit; UTC when the column is aware.
    per_minute = {"s": 60, "ms": 60_000, "us": 60_000_000, "ns": 60_000_000_000}[ts.dt.unit]
    minutes = values // per_minute
    return minutes + IST_OFFSET_MS // 60_000 if ts.dt.tz is not None else minutes


def resample_intraday(minutes: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """5m, 15m or 1h candles from one-minute ones, each session starting at 09:15.

    Only finished candles are returned: a candle still forming at the decision is left out,
    so a signal never changes after it is acted on. Grouping is done with array arithmetic
    because this runs at every decision of an intraday backtest.
    """
    if minutes.empty:
        return minutes
    size = INTRADAY_MINUTES[timeframe]
    if not minutes["ts"].is_monotonic_increasing:
        minutes = minutes.sort_values("ts", kind="stable")
    wall = _wall_minutes(minutes["ts"])
    day = wall // 1440
    offset = wall - day * 1440 - SESSION_START_MIN
    # Each candle's start, in wall-clock minutes; minutes before 09:15 fall in earlier buckets.
    start = day * 1440 + SESSION_START_MIN + np.floor_divide(offset, size) * size
    edges = np.flatnonzero(np.diff(start)) + 1
    first = np.concatenate(([0], edges))
    last = np.concatenate((edges, [len(start)])) - 1
    highs = minutes["high"].to_numpy(float)
    lows = minutes["low"].to_numpy(float)
    count = last - first + 1
    frame = pd.DataFrame(
        {
            "ts": minutes["ts"].iloc[first].reset_index(drop=True),
            "open": minutes["open"].to_numpy(float)[first],
            "high": np.maximum.reduceat(highs, first),
            "low": np.minimum.reduceat(lows, first),
            "close": minutes["close"].to_numpy(float)[last],
        }
    )
    # The last candle is finished only when all its minutes have ended.
    if count[-1] < size:
        last_start = int(start[-1])
        session_end = (last_start // 1440) * 1440 + SESSION_END_MIN
        if last_start + size < session_end:
            frame = frame.iloc[:-1]
    return frame.reset_index(drop=True)


def resample(daily: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Daily candles, or weekly or monthly ones built from them. Expects a `ts` column."""
    if daily.empty or timeframe == "1d":
        return daily.reset_index(drop=True)
    times = pd.to_datetime(daily["ts"])
    if getattr(times.dt, "tz", None) is not None:
        times = times.dt.tz_localize(None)
    key = times.dt.to_period("W-FRI" if timeframe == "1w" else "M")
    grouped = daily.groupby(key.to_numpy(), sort=True)
    return pd.DataFrame(
        {
            "ts": grouped["ts"].last().to_numpy(),
            "open": grouped["open"].first().to_numpy(float),
            "high": grouped["high"].max().to_numpy(float),
            "low": grouped["low"].min().to_numpy(float),
            "close": grouped["close"].last().to_numpy(float),
        }
    )


class Frames:
    """Candles per timeframe and indicator series, computed once per decision."""

    def __init__(self, daily: pd.DataFrame, minutes: pd.DataFrame | None = None):
        self.daily = daily
        self.minutes = minutes if minutes is not None else pd.DataFrame()
        self._frames: dict[tuple[str, str], pd.DataFrame] = {}
        self._series: dict[tuple, pd.Series] = {}

    def frame(self, timeframe: str, candles: str = "normal") -> pd.DataFrame:
        key = (timeframe, candles)
        if key not in self._frames:
            if candles == "heikin_ashi":
                self._frames[key] = heikin_ashi(self.frame(timeframe))
            else:
                self._frames[key] = (
                    resample_intraday(self.minutes, timeframe)
                    if timeframe in INTRADAY_MINUTES
                    else resample(self.daily, timeframe)
                )
        return self._frames[key]

    def series(self, timeframe: str, operand: Operand, candles: str = "normal") -> pd.Series:
        indicator = INDICATORS[operand.name]
        params = {p.name: operand.params.get(p.name, p.default) for p in indicator.params}
        key = (timeframe, candles, operand.name, tuple(sorted(params.items())))
        if key not in self._series:
            result = indicator.compute(self.frame(timeframe, candles), **params)
            if isinstance(result, dict):
                for name, values in result.items():
                    self._series[(*key, name)] = values
                self._series[key] = pd.Series(dtype=float)
            else:
                self._series[key] = result
        return self._series[(*key, operand.field)] if operand.field else self._series[key]

    def value(
        self, timeframe: str, operand: Operand, extra_back: int = 0, candles: str = "normal"
    ) -> float | None:
        if operand.kind == "value":
            return operand.value
        values = self.series(timeframe, operand, candles)
        position = len(values) - 1 - operand.bars_ago - extra_back
        if position < 0:
            return None
        found = values.iloc[position]
        return None if pd.isna(found) else float(found)


def operand_text(operand: Operand) -> str:
    if operand.kind == "value":
        return f"{operand.value:g}"
    indicator = INDICATORS[operand.name]
    params = ",".join(f"{operand.params.get(p.name, p.default):g}" for p in indicator.params)
    text = operand.name.upper() if len(operand.name) <= 4 else operand.name.replace("_", " ")
    if params:
        text += f"({params})"
    if operand.field:
        text += f" {indicator.fields[operand.field]}"
    if operand.bars_ago:
        text += f" {operand.bars_ago} candles ago"
    return text


OPS = {
    "above": "above",
    "below": "below",
    "crosses_above": "crosses above",
    "crosses_below": "crosses below",
}


def rule_text(rule: CompareRule | PatternRule) -> str:
    frame = TIMEFRAMES[rule.timeframe].lower()
    if rule.candles == "heikin_ashi":
        frame += " Heikin-Ashi"
    if isinstance(rule, PatternRule):
        when = "on the latest candle" if rule.within == 1 else f"in the last {rule.within} candles"
        verb = "" if rule.present else "no "
        return f"{verb}{frame} {PATTERNS[rule.pattern].label.lower()} {when}"
    return f"{frame} {operand_text(rule.left)} {OPS[rule.op]} {operand_text(rule.right)}"


def rule_failure(rule: CompareRule | PatternRule, frames: Frames) -> str | None:
    """Why the rule does not hold now, or None when it holds."""
    label = f"entry rule: {rule_text(rule)}"
    kind = rule.candles
    if rule.timeframe in INTRADAY_MINUTES:
        if frames.minutes.empty:
            return f"{label} needs one-minute candles"
    elif frames.daily.empty:
        return f"{label} has no price history"
    if isinstance(rule, PatternRule):
        candles = frames.frame(rule.timeframe, kind)
        if len(candles) < 3:
            return f"{label} needs more candles"
        seen = bool(PATTERNS[rule.pattern].detect(candles).iloc[-rule.within :].fillna(False).any())
        return None if seen == rule.present else f"{label} does not hold"
    left = frames.value(rule.timeframe, rule.left, candles=kind)
    right = frames.value(rule.timeframe, rule.right, candles=kind)
    if left is None or right is None:
        return f"{label} is not available yet"
    if rule.op == "above":
        return None if left > right else f"{label} does not hold"
    if rule.op == "below":
        return None if left < right else f"{label} does not hold"
    left_before = frames.value(rule.timeframe, rule.left, 1, kind)
    right_before = frames.value(rule.timeframe, rule.right, 1, kind)
    if left_before is None or right_before is None:
        return f"{label} is not available yet"
    crossed = (
        left_before <= right_before and left > right
        if rule.op == "crosses_above"
        else left_before >= right_before and left < right
    )
    return None if crossed else f"{label} does not hold"


def catalog() -> dict:
    """What the strategy editor can offer."""
    return {
        "timeframes": [{"value": k, "label": v} for k, v in TIMEFRAMES.items()],
        "candle_types": [{"value": k, "label": v} for k, v in CANDLE_TYPES.items()],
        "operators": [{"value": k, "label": v} for k, v in OPS.items()],
        "indicators": [
            {
                "name": name,
                "label": indicator.label,
                "unit": indicator.unit,
                "description": indicator.description,
                "params": [
                    {
                        "name": p.name,
                        "label": p.label,
                        "default": p.default,
                        "min": p.low,
                        "max": p.high,
                        "whole": p.whole,
                    }
                    for p in indicator.params
                ],
                "fields": [{"value": k, "label": v} for k, v in indicator.fields.items()],
                "intraday_only": indicator.intraday_only,
                "overlay": name in CHART_OVERLAY,
            }
            for name, indicator in INDICATORS.items()
        ],
        "patterns": [
            {"name": name, "label": p.label, "bias": p.bias, "description": p.description}
            for name, p in PATTERNS.items()
        ],
    }

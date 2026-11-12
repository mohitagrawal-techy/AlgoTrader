"""Candles, indicator lines and rule signals for the strategy builder's chart.

Everything is computed with the same indicator and rule code the backtests use, so a line on
the chart is exactly what a strategy reads, and a signal marker is a candle where its rules
held."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, ValidationError

from algoverse.lab.technical import (
    CHART_OVERLAY,
    CHART_SEPARATE_FIELDS,
    INDICATORS,
    INTRADAY_MINUTES,
    PATTERNS,
    TIMEFRAMES,
    CandleType,
    CompareRule,
    Frames,
    Operand,
    PatternRule,
    TechRule,
    rule_text,
)

IST_SECONDS = 19_800
MAX_STUDIES = 12
MAX_CANDLES = 4000
# Sessions shown by default on minute charts, and the most that can be asked for.
DEFAULT_SESSIONS = {"5m": 10, "15m": 30, "1h": 90}
MAX_SESSIONS = 400


class Study(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    name: str
    params: dict[str, float] = Field(default_factory=dict)


class ChartRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    timeframe: Literal["5m", "15m", "1h", "1d", "1w", "1mo"] = "1d"
    candles: CandleType = "normal"
    sessions: int | None = Field(default=None, ge=1, le=MAX_SESSIONS)
    studies: list[Study] = Field(default_factory=list, max_length=MAX_STUDIES)
    rules: list[TechRule] = Field(default_factory=list, max_length=8)
    any_rules: list[TechRule] = Field(default_factory=list, max_length=8)


def _clean(values: pd.Series) -> list[float | None]:
    array = values.to_numpy(dtype=float, na_value=np.nan)
    return [None if not np.isfinite(v) else round(float(v), 4) for v in array]


def _times(frame: pd.DataFrame) -> list[int]:
    """Epoch seconds shifted to IST wall-clock, because the chart shows times in UTC."""
    stamps = pd.to_datetime(frame["ts"])
    if getattr(stamps.dt, "tz", None) is None:
        stamps = stamps.dt.tz_localize("Asia/Kolkata")
    # Independent of the stored resolution, which may be nano- or microseconds.
    seconds = (stamps - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)
    return [int(s) + IST_SECONDS for s in seconds]


def _operand_series(frames: Frames, timeframe: str, operand: Operand, candles: str) -> pd.Series:
    """The operand's value at every candle, read `bars_ago` back as a rule reads it."""
    index = frames.frame(timeframe, candles).index
    if operand.kind == "value":
        return pd.Series(operand.value, index=index, dtype=float)
    series = frames.series(timeframe, operand, candles).astype(float)
    return series.shift(operand.bars_ago)


def rule_series(rule: CompareRule | PatternRule, frames: Frames) -> pd.Series:
    """Whether the rule held at each candle of its timeframe. Matches `rule_failure`, which
    checks only the latest candle."""
    candles = rule.candles
    frame = frames.frame(rule.timeframe, candles)
    if isinstance(rule, PatternRule):
        seen = PATTERNS[rule.pattern].detect(frame).fillna(False).astype(float)
        hit = seen.rolling(rule.within, min_periods=1).max() > 0
        held = hit if rule.present else ~hit
        # The latest-candle check needs at least three candles.
        held.iloc[: min(2, len(held))] = False
        return held
    left = _operand_series(frames, rule.timeframe, rule.left, candles)
    right = _operand_series(frames, rule.timeframe, rule.right, candles)
    known = left.notna() & right.notna()
    if rule.op == "above":
        return known & (left > right)
    if rule.op == "below":
        return known & (left < right)
    before_left, before_right = left.shift(1), right.shift(1)
    known &= before_left.notna() & before_right.notna()
    if rule.op == "crosses_above":
        return known & (before_left <= before_right) & (left > right)
    return known & (before_left >= before_right) & (left < right)


def _study(frames: Frames, request: ChartRequest, study: Study, keep: slice) -> dict:
    indicator = INDICATORS.get(study.name)
    base = {"id": study.id, "name": study.name}
    if indicator is None:
        return base | {"error": "Unknown indicator", "lines": []}
    if indicator.intraday_only and request.timeframe not in INTRADAY_MINUTES:
        return base | {
            "label": indicator.label,
            "error": f"{indicator.label} needs 5-minute, 15-minute or hourly candles",
            "lines": [],
        }
    params = {p.name: study.params.get(p.name, p.default) for p in indicator.params}
    # Fields of a price overlay that are not prices, such as band width, stay rule inputs.
    # Drawn in a pane of their own they read as clutter next to the bands.
    fields = [f for f in indicator.fields if (study.name, f) not in CHART_SEPARATE_FIELDS] or [None]
    try:
        operands = [Operand(name=study.name, params=params, field=f) for f in fields]
    except ValidationError as exc:
        return base | {
            "label": indicator.label,
            "error": exc.errors()[0]["msg"].removeprefix("Value error, "),
            "lines": [],
        }
    short = ", ".join(f"{v:g}" for v in params.values())
    lines = []
    for operand in operands:
        values = frames.series(request.timeframe, operand, request.candles).astype(float)
        overlay = (
            study.name in CHART_OVERLAY and (study.name, operand.field) not in CHART_SEPARATE_FIELDS
        )
        lines.append(
            {
                "field": operand.field,
                "label": indicator.fields.get(operand.field, indicator.label)
                if operand.field
                else indicator.label,
                "overlay": overlay,
                "values": _clean(values.iloc[keep]),
            }
        )
    return base | {
        "label": indicator.label + (f" ({short})" if short else ""),
        "unit": indicator.unit,
        "params": params,
        "lines": lines,
    }


def _signals(frames: Frames, request: ChartRequest, length: int, keep: slice) -> dict:
    """Candles where every entry rule on this timeframe held. Rules on other timeframes
    cannot be placed on these candles, so they are listed instead."""
    shown, other, problems = [], [], []
    for group, rules in (("all", request.rules), ("any", request.any_rules)):
        for rule in rules:
            if rule.timeframe != request.timeframe:
                other.append(rule_text(rule))
                continue
            try:
                shown.append((group, rule_text(rule), rule_series(rule, frames)))
            except (KeyError, ValueError) as exc:
                problems.append(f"{rule_text(rule)}: {exc}")
    if not shown:
        return {"rules": [], "other_timeframes": other, "problems": problems, "hits": []}
    held = pd.Series(True, index=range(length))
    any_held = None
    for group, _, series in shown:
        values = series.reset_index(drop=True).fillna(False).astype(bool)
        if group == "all":
            held &= values
        else:
            any_held = values if any_held is None else (any_held | values)
    if any_held is not None:
        held &= any_held
    window = held.iloc[keep]
    return {
        "rules": [
            {
                "group": group,
                "text": text,
                "count": int(series.reset_index(drop=True).iloc[keep].fillna(False).sum()),
            }
            for group, text, series in shown
        ],
        "other_timeframes": other,
        "problems": problems,
        # Positions among the candles shown.
        "hits": [int(i) for i in np.flatnonzero(window.to_numpy())],
    }


def chart(daily: pd.DataFrame, minutes: pd.DataFrame, request: ChartRequest) -> dict:
    frames = Frames(daily, minutes)
    intraday = request.timeframe in INTRADAY_MINUTES
    frame = frames.frame(request.timeframe, request.candles)
    if frame.empty:
        source = "one-minute" if intraday else "daily"
        return {
            "symbol": request.symbol,
            "timeframe": request.timeframe,
            "empty": f"No {source} candles are stored for {request.symbol}.",
        }
    if intraday:
        days = pd.to_datetime(frame["ts"]).dt.date
        wanted = request.sessions or DEFAULT_SESSIONS[request.timeframe]
        distinct = days.drop_duplicates()
        first_day = distinct.iloc[max(0, len(distinct) - wanted)]
        start = int(np.flatnonzero((days >= first_day).to_numpy())[0])
    else:
        start = 0
    start = max(start, len(frame) - MAX_CANDLES)
    keep = slice(start, len(frame))
    shown = frame.iloc[keep]
    return {
        "symbol": request.symbol,
        "timeframe": request.timeframe,
        "timeframe_label": TIMEFRAMES[request.timeframe],
        "candles_type": request.candles,
        "source": "Index one-minute candles"
        if intraday
        else "Nearest-month future, else the index, as the backtests use",
        "time": _times(shown),
        "open": _clean(shown["open"]),
        "high": _clean(shown["high"]),
        "low": _clean(shown["low"]),
        "close": _clean(shown["close"]),
        "studies": [_study(frames, request, study, keep) for study in request.studies],
        "signals": _signals(frames, request, len(frame), keep),
    }

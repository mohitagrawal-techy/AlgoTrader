"""Market state shared by every candidate in one evaluation cycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd

from algoverse.analytics.iv import (
    IvRanking,
    atm_iv,
    iv_ranking,
    skew_25d,
    term_slope,
    term_structure,
)
from algoverse.analytics.levels import (
    Level,
    Zone,
    camarilla_pivots,
    classic_pivots,
    confirmed_swings,
    confluence_zones,
)
from algoverse.analytics.positioning import classify_buildup, max_pain, oi_walls, put_call_ratio
from algoverse.analytics.regime import RegimeSnapshot, ema, realised_vol, regime_snapshot
from algoverse.evaluation.config import EventCalendar

BUILDUP_RANGE = 0.03
IV_HISTORY_MIN_DAYS_TO_EXPIRY = 5


@dataclass(frozen=True)
class ExpiryStats:
    atm_iv: float | None
    forward: float
    forward_implied: bool
    T: float
    expected_move: float | None
    skew: float | None
    pcr: float | None
    max_pain: float | None
    walls: dict[str, list[float]]
    buildup: float | None  # -1 fully bearish to +1 fully bullish.
    zones: tuple[Zone, ...]


@dataclass(frozen=True)
class IvContext:
    ranking: IvRanking | None
    source: str


@dataclass(frozen=True)
class MarketContext:
    symbol: str
    now: datetime
    spot: float
    r: float
    regime: RegimeSnapshot | None
    realised_vol: float | None
    rv_ranking: IvRanking | None
    iv: IvContext
    vix_ranking: IvRanking | None
    vix_change_pct: float | None
    term_slope: float | None
    by_expiry: dict[date, ExpiryStats]
    events: EventCalendar
    # Daily candles up to the decision, including the session in progress when it is known.
    bars: pd.DataFrame = field(default_factory=pd.DataFrame)


def completed_bars(bars: pd.DataFrame, today: date) -> pd.DataFrame:
    """Drop today's unfinished bar so signals use only completed sessions."""
    if bars.empty:
        return bars
    return bars[pd.to_datetime(bars["ts"]).dt.date < today].reset_index(drop=True)


def _buildup_score(chain: pd.DataFrame, forward: float) -> float | None:
    near = chain[(chain["strike"] / forward - 1).abs() <= BUILDUP_RANGE]
    if near.empty:
        return None
    labels = classify_buildup(near)
    weight = (near["oi"] - near["prev_oi"]).abs()
    bullish_call = labels.isin(["long_buildup", "short_covering"])
    bearish_call = labels.isin(["short_buildup", "long_unwinding"])
    is_call = near["option_type"] == "CE"
    bullish = weight[(is_call & bullish_call) | (~is_call & bearish_call)].sum()
    bearish = weight[(is_call & bearish_call) | (~is_call & bullish_call)].sum()
    total = bullish + bearish
    return None if total == 0 else float((bullish - bearish) / total)


def _base_levels(bars: pd.DataFrame) -> list[Level]:
    if bars.empty:
        return []
    last = bars.iloc[-1]
    high, low, close = float(last["high"]), float(last["low"]), float(last["close"])
    levels = [
        Level(price, "pivot")
        for name, price in classic_pivots(high, low, close).items()
        if name in ("P", "R1", "S1", "R2", "S2")
    ]
    levels += [
        Level(price, "camarilla")
        for name, price in camarilla_pivots(high, low, close).items()
        if name in ("R3", "S3")
    ]
    swings = confirmed_swings(bars)
    for column in ("swing_high", "swing_low"):
        levels += [Level(float(p), "swing") for p in swings[column].dropna().tail(10)]
    for span in (20, 50):
        value = ema(bars["close"], span).iloc[-1]
        if pd.notna(value):
            levels.append(Level(float(value), "ema"))
    return levels


def _expiry_stats(group: pd.DataFrame, base_levels: list[Level]) -> ExpiryStats:
    forward = float(group["forward"].iloc[0])
    T = float(group["T"].iloc[0])
    iv = atm_iv(group)
    pain = max_pain(group)
    walls = oi_walls(group)
    levels = list(base_levels)
    levels += [Level(k, "oi_wall") for k in walls["resistance"][:1] + walls["support"][:1]]
    if pain is not None:
        levels.append(Level(pain, "max_pain"))
    return ExpiryStats(
        atm_iv=iv,
        forward=forward,
        forward_implied=bool(group["forward_implied"].all()),
        T=T,
        expected_move=None if iv is None or T <= 0 else forward * iv * float(np.sqrt(T)),
        skew=skew_25d(group),
        pcr=put_call_ratio(group),
        max_pain=pain,
        walls=walls,
        buildup=_buildup_score(group, forward),
        zones=tuple(confluence_zones(levels)),
    )


def reference_atm_iv(chain: pd.DataFrame) -> float | None:
    """ATM IV of the nearest expiry far enough away to avoid expiry-day distortion."""
    minimum = IV_HISTORY_MIN_DAYS_TO_EXPIRY / 365
    eligible = chain[chain["T"] >= minimum]
    if eligible.empty:
        return None
    return atm_iv(eligible[eligible["expiry"] == eligible["expiry"].min()])


def _rv_context(bars: pd.DataFrame) -> tuple[float | None, IvRanking | None]:
    if bars.empty:
        return None, None
    series = realised_vol(bars["close"]).dropna()
    if series.empty:
        return None, None
    current = float(series.iloc[-1])
    return current, iv_ranking(series.iloc[:-1], current)


def _vix_context(vix_bars: pd.DataFrame) -> tuple[IvRanking | None, float | None]:
    if vix_bars.empty:
        return None, None
    close = vix_bars["close"].dropna()
    if close.empty:
        return None, None
    current = float(close.iloc[-1])
    change = None if len(close) < 6 else float((current / close.iloc[-6] - 1) * 100)
    return iv_ranking(close.iloc[:-1], current), change


def build_context(
    symbol: str,
    now: datetime,
    chain: pd.DataFrame,
    bars: pd.DataFrame,
    vix_bars: pd.DataFrame,
    iv_history: pd.Series,
    events: EventCalendar,
    r: float,
    today_bar: dict | None = None,
) -> MarketContext:
    """Assemble context. `chain` is enriched. All history is trimmed to before today.

    `today_bar` is the session's own candle so far (open, high, low, close). Only rules that
    read candles use it; the other signals stay on completed sessions."""
    bars = completed_bars(bars, now.date())
    decision_bars = bars[["ts", "open", "high", "low", "close"]] if not bars.empty else bars
    if today_bar is not None and not bars.empty:
        today = pd.DataFrame(
            [
                {
                    "ts": pd.Timestamp(now.date()).tz_localize(pd.to_datetime(bars["ts"]).dt.tz),
                    **{k: float(today_bar[k]) for k in ("open", "high", "low", "close")},
                }
            ]
        )
        decision_bars = pd.concat([decision_bars, today], ignore_index=True)
    vix_bars = completed_bars(vix_bars, now.date())
    live = chain[chain["T"] > 0]
    base_levels = _base_levels(bars)
    by_expiry = {
        expiry: _expiry_stats(group, base_levels)
        for expiry, group in live.groupby("expiry", sort=True)
    }
    rv, rv_rank = _rv_context(bars)
    vix_rank, vix_change = _vix_context(vix_bars)
    current_iv = reference_atm_iv(live)
    own = None if current_iv is None else iv_ranking(iv_history, current_iv)
    iv_context = (
        IvContext(own, "own_snapshots")
        if own is not None
        else IvContext(vix_rank, "india_vix_proxy")
    )
    return MarketContext(
        symbol=symbol,
        now=now,
        spot=float(chain["spot"].iloc[0]),
        r=r,
        regime=None if bars.empty else regime_snapshot(bars),
        realised_vol=rv,
        rv_ranking=rv_rank,
        iv=iv_context,
        vix_ranking=vix_rank,
        vix_change_pct=vix_change,
        term_slope=term_slope(term_structure(live)) if not live.empty else None,
        by_expiry=by_expiry,
        events=events,
        bars=decision_bars,
    )

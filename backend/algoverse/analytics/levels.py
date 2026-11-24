"""Support and resistance levels and confluence zones."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Level:
    price: float
    method: str


@dataclass(frozen=True)
class Zone:
    center: float
    low: float
    high: float
    methods: tuple[str, ...]

    @property
    def strength(self) -> int:
        return len(self.methods)


def classic_pivots(high: float, low: float, close: float) -> dict[str, float]:
    pivot = (high + low + close) / 3
    span = high - low
    return {
        "P": pivot,
        "R1": 2 * pivot - low,
        "S1": 2 * pivot - high,
        "R2": pivot + span,
        "S2": pivot - span,
        "R3": high + 2 * (pivot - low),
        "S3": low - 2 * (high - pivot),
    }


def camarilla_pivots(high: float, low: float, close: float) -> dict[str, float]:
    span = high - low
    return {
        "R3": close + span * 1.1 / 4,
        "R4": close + span * 1.1 / 2,
        "S3": close - span * 1.1 / 4,
        "S4": close - span * 1.1 / 2,
    }


def confirmed_swings(bars: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    """Swing prices, reported on the bar where they become knowable.

    A swing needs `n` later bars to confirm it, so each value appears `n` bars after the swing
    bar. This keeps the output free of lookahead in backtests.
    """
    window = 2 * n + 1
    highs = bars["high"].where(bars["high"] == bars["high"].rolling(window, center=True).max())
    lows = bars["low"].where(bars["low"] == bars["low"].rolling(window, center=True).min())
    return pd.DataFrame({"swing_high": highs.shift(n), "swing_low": lows.shift(n)})


def session_vwap(bars: pd.DataFrame) -> pd.Series:
    """VWAP of typical price, reset each session. Needs a datetime index."""
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3
    sessions = bars.index.date
    notional = (typical * bars["volume"]).groupby(sessions).cumsum()
    volume = bars["volume"].groupby(sessions).cumsum().replace(0.0, np.nan)
    return notional / volume


def confluence_zones(levels: Iterable[Level], tolerance_pct: float = 0.15) -> list[Zone]:
    """Cluster nearby levels and rank zones by how many distinct methods agree."""
    ordered = sorted(levels, key=lambda level: level.price)
    if not ordered:
        return []
    clusters: list[list[Level]] = [[ordered[0]]]
    for level in ordered[1:]:
        previous = clusters[-1][-1].price
        if (level.price - previous) / previous * 100 <= tolerance_pct:
            clusters[-1].append(level)
        else:
            clusters.append([level])
    zones = [
        Zone(
            center=float(np.mean([level.price for level in cluster])),
            low=cluster[0].price,
            high=cluster[-1].price,
            methods=tuple(sorted({level.method for level in cluster})),
        )
        for cluster in clusters
    ]
    return sorted(zones, key=lambda zone: (-zone.strength, zone.center))

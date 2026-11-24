"""Volatility metrics. Chain inputs must come from `enrich_chain`."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

MIN_HISTORY = 30


@dataclass(frozen=True)
class IvRanking:
    """IV rank and percentile (0-100), with the sample size behind them."""

    rank: float
    percentile: float
    sample_size: int


def iv_ranking(
    history: pd.Series, current: float, lookback: int = 252, min_history: int = MIN_HISTORY
) -> IvRanking | None:
    """Rank `current` against prior observations. `history` must exclude the current value."""
    past = history.dropna().tail(lookback)
    if len(past) < min_history or not np.isfinite(current):
        return None
    low, high = past.min(), past.max()
    rank = 50.0 if high == low else float((current - low) / (high - low) * 100)
    percentile = float((past < current).mean() * 100)
    return IvRanking(
        rank=float(np.clip(rank, 0, 100)), percentile=percentile, sample_size=len(past)
    )


def atm_iv(expiry_chain: pd.DataFrame) -> float | None:
    """Average call and put IV at the strike closest to the forward, for one expiry."""
    priced = expiry_chain.dropna(subset=["iv"])
    if priced.empty:
        return None
    forward = float(priced["forward"].iloc[0])
    both = priced.groupby("strike")["option_type"].nunique()
    strikes = both[both == 2].index.to_series()
    if strikes.empty:
        return None
    atm = strikes.sub(forward).abs().idxmin()
    return float(priced.loc[priced["strike"] == atm, "iv"].mean())


def term_structure(chain: pd.DataFrame) -> pd.DataFrame:
    """Return expiry, T and atm_iv by ascending expiry."""
    rows = [
        {"expiry": expiry, "T": float(group["T"].iloc[0]), "atm_iv": atm_iv(group)}
        for expiry, group in chain.groupby("expiry", sort=True)
    ]
    return pd.DataFrame(rows, columns=["expiry", "T", "atm_iv"]).dropna(subset=["atm_iv"])


def term_slope(structure: pd.DataFrame) -> float | None:
    """Far minus near ATM IV. Negative means backwardation."""
    if len(structure) < 2:
        return None
    return float(structure["atm_iv"].iloc[-1] - structure["atm_iv"].iloc[0])


def _iv_at_delta(side: pd.DataFrame, target: float) -> float | None:
    ordered = side.dropna(subset=["iv", "delta"]).sort_values("delta")
    if ordered.empty:
        return None
    deltas = ordered["delta"].to_numpy()
    if not deltas[0] <= target <= deltas[-1]:
        return None
    return float(np.interp(target, deltas, ordered["iv"].to_numpy()))


def skew_25d(expiry_chain: pd.DataFrame) -> float | None:
    """25-delta put IV minus 25-delta call IV, for one expiry."""
    calls = expiry_chain[(expiry_chain["option_type"] == "CE") & (expiry_chain["delta"] <= 0.5)]
    puts = expiry_chain[(expiry_chain["option_type"] == "PE") & (expiry_chain["delta"] >= -0.5)]
    call_iv = _iv_at_delta(calls, 0.25)
    put_iv = _iv_at_delta(puts, -0.25)
    return None if call_iv is None or put_iv is None else put_iv - call_iv

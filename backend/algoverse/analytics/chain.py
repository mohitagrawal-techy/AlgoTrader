"""Adds computed mids, forwards, IV and Greeks to a raw chain snapshot."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from algoverse.analytics.pricing import black76_greeks, implied_vol

IST = ZoneInfo("Asia/Kolkata")
EXPIRY_TIME = time(15, 30)
SECONDS_PER_YEAR = 365.0 * 86400.0
PARITY_STRIKES = 5


def years_to_expiry(expiry: date, now: datetime) -> float:
    return (datetime.combine(expiry, EXPIRY_TIME, tzinfo=IST) - now).total_seconds() / (
        SECONDS_PER_YEAR
    )


def mid_prices(chain: pd.DataFrame) -> pd.Series:
    """Mid of a two-sided quote. One-sided or crossed quotes give NaN, never a stale last price."""
    two_sided = (chain["bid"] > 0) & (chain["ask"] > 0) & (chain["ask"] >= chain["bid"])
    return ((chain["bid"] + chain["ask"]) / 2).where(two_sided)


def _mean_by_strike(strikes: np.ndarray, values: np.ndarray) -> dict[float, float]:
    unique, slot = np.unique(strikes, return_inverse=True)
    sums = np.bincount(slot, weights=values, minlength=len(unique))
    counts = np.bincount(slot, minlength=len(unique))
    return dict(zip(unique.tolist(), (sums / counts).tolist(), strict=True))


def implied_forward(chain: pd.DataFrame, spot: float, T: float, r: float) -> float | None:
    """Median parity forward across near-the-money strikes, or None without usable quotes.

    Plain numpy: this runs for every expiry at every decision of a replay, where a pandas
    pivot table was the slowest step."""
    mid = mid_prices(chain).to_numpy(float)
    strikes = chain["strike"].to_numpy(float)
    kind = chain["option_type"].to_numpy()
    quoted = ~np.isnan(mid)
    calls_at = quoted & (kind == "CE")
    puts_at = quoted & (kind == "PE")
    if not calls_at.any() or not puts_at.any():
        return None
    calls = _mean_by_strike(strikes[calls_at], mid[calls_at])
    puts = _mean_by_strike(strikes[puts_at], mid[puts_at])
    both = np.array(sorted(calls.keys() & puts.keys()), dtype=float)
    if both.size == 0:
        return None
    # Nearest strikes to spot; on a tie the lower strike comes first.
    nearest = both[np.argsort(np.abs(both - spot), kind="stable")[:PARITY_STRIKES]]
    parity = [k + (calls[k] - puts[k]) * np.exp(r * T) for k in nearest.tolist()]
    return float(np.median(parity))


def enrich_chain(chain: pd.DataFrame, now: datetime, r: float) -> pd.DataFrame:
    """Return a copy with mid, T, forward, forward_implied, iv, delta, gamma, theta and vega."""
    groups = []
    for expiry, group in chain.groupby("expiry", sort=True):
        group = group.copy()
        spot = float(group["spot"].iloc[0])
        T = years_to_expiry(expiry, now)
        forward = implied_forward(group, spot, T, r) if T > 0 else None
        group["forward_implied"] = forward is not None
        if forward is None:
            forward = spot * np.exp(r * max(T, 0.0))
        group["mid"] = mid_prices(group)
        group["T"] = T
        group["forward"] = forward
        is_call = (group["option_type"] == "CE").to_numpy()
        strikes = group["strike"].to_numpy(float)
        iv = implied_vol(group["mid"].to_numpy(float), forward, strikes, T, r, is_call)
        greeks = black76_greeks(forward, strikes, T, iv, r, is_call, spot=spot)
        group["iv"] = iv
        for name in ("delta", "gamma", "theta", "vega"):
            group[name] = getattr(greeks, name)
        groups.append(group)
    if not groups:
        return chain.copy()
    return pd.concat(groups, ignore_index=True)

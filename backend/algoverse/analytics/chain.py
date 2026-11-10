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


def implied_forward(chain: pd.DataFrame, spot: float, T: float, r: float) -> float | None:
    """Median parity forward across near-the-money strikes, or None without usable quotes."""
    quoted = chain.assign(mid=mid_prices(chain)).dropna(subset=["mid"])
    sides = quoted.pivot_table(index="strike", columns="option_type", values="mid")
    if not {"CE", "PE"} <= set(sides.columns):
        return None
    sides = sides.dropna(subset=["CE", "PE"])
    if sides.empty:
        return None
    nearest = sides.index.to_series().sub(spot).abs().nsmallest(PARITY_STRIKES).index
    near = sides.loc[nearest]
    return float(np.median(near.index + (near["CE"] - near["PE"]) * np.exp(r * T)))


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

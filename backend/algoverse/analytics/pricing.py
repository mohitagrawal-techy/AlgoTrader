"""Vectorised Black-76 pricing, Greeks and implied volatility.

Conventions: `T` is calendar years (days / 365), `sigma` and `r` are decimals, and `is_call`
is a boolean or boolean array. NSE options are European, so Black-76 applies directly.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

IV_LOW = 1e-4
IV_HIGH = 5.0
IV_ITERATIONS = 80


def _d1_d2(F, K, T, sigma):
    root_t = np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sigma**2 * T) / (sigma * root_t)
    return d1, d1 - sigma * root_t


def black76_price(F, K, T, sigma, r, is_call):
    F, K, T, sigma, r = np.broadcast_arrays(*(np.asarray(x, float) for x in (F, K, T, sigma, r)))
    is_call = np.asarray(is_call, bool)
    discount = np.exp(-r * np.maximum(T, 0.0))
    intrinsic = np.where(is_call, np.maximum(F - K, 0.0), np.maximum(K - F, 0.0))
    live = (T > 0) & (sigma > 0)
    safe_t = np.where(live, T, 1.0)
    safe_sigma = np.where(live, sigma, 1.0)
    d1, d2 = _d1_d2(F, K, safe_t, safe_sigma)
    call = F * norm.cdf(d1) - K * norm.cdf(d2)
    put = K * norm.cdf(-d2) - F * norm.cdf(-d1)
    undiscounted = np.where(is_call, call, put)
    return discount * np.where(live, undiscounted, intrinsic)


@dataclass(frozen=True)
class Greeks:
    """Per-unit Greeks. Delta and gamma are with respect to spot when spot is supplied.

    Theta is per calendar day with the forward held constant. Vega is per 1 volatility point.
    """

    price: np.ndarray
    delta: np.ndarray
    gamma: np.ndarray
    theta: np.ndarray
    vega: np.ndarray


def black76_greeks(F, K, T, sigma, r, is_call, spot=None) -> Greeks:
    F, K, T, sigma, r = np.broadcast_arrays(*(np.asarray(x, float) for x in (F, K, T, sigma, r)))
    is_call = np.asarray(is_call, bool)
    scale = np.ones_like(F) if spot is None else F / np.asarray(spot, float)
    price = black76_price(F, K, T, sigma, r, is_call)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1, _ = _d1_d2(F, K, T, sigma)
        discount = np.exp(-r * T)
        pdf = norm.pdf(d1)
        root_t = np.sqrt(T)
        delta = discount * np.where(is_call, norm.cdf(d1), norm.cdf(d1) - 1.0) * scale
        gamma = discount * pdf / (F * sigma * root_t) * scale**2
        theta = (r * price - discount * F * pdf * sigma / (2.0 * root_t)) / 365.0
        vega = discount * F * pdf * root_t / 100.0
    return Greeks(price, delta, gamma, theta, vega)


def implied_vol(price, F, K, T, r, is_call):
    """Bisection IV. Returns NaN where the price is outside no-arbitrage bounds."""
    price, F, K, T, r = np.broadcast_arrays(*(np.asarray(x, float) for x in (price, F, K, T, r)))
    is_call = np.broadcast_to(np.asarray(is_call, bool), price.shape)
    low = np.full(price.shape, IV_LOW)
    high = np.full(price.shape, IV_HIGH)
    valid = np.isfinite(price) & np.isfinite(F) & (T > 0) & (price > 0)
    valid &= black76_price(F, K, T, low, r, is_call) < price
    valid &= black76_price(F, K, T, high, r, is_call) > price
    for _ in range(IV_ITERATIONS):
        mid = 0.5 * (low + high)
        too_low = black76_price(F, K, T, mid, r, is_call) < price
        low = np.where(too_low, mid, low)
        high = np.where(too_low, high, mid)
    return np.where(valid, 0.5 * (low + high), np.nan)

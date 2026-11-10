"""Builds broker-shaped chains from daily data, using modelled bid/ask."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from algoverse.backtest.config import SpreadConfig
from algoverse.brokers.base import CHAIN_COLUMNS


def spread_percent(
    moneyness_pct: np.ndarray, lots_traded: np.ndarray, config: SpreadConfig
) -> np.ndarray:
    return (
        config.base_pct
        + config.moneyness_pct_per_pct * moneyness_pct
        + config.illiquidity_pct / np.sqrt(1 + lots_traded)
    )


def contract_key(symbol: str, frame: pd.DataFrame) -> pd.Series:
    return (
        symbol
        + "|"
        + frame["expiry"].astype(str)
        + "|"
        + frame["strike"].map("{:g}".format)
        + "|"
        + frame["option_type"]
    )


def modelled_chain(
    symbol: str,
    options: pd.DataFrame,
    price_column: str,
    spot: float,
    now: datetime,
    config: SpreadConfig,
    multiplier: float = 1.0,
) -> pd.DataFrame:
    """Return a raw chain for one moment. Untraded contracts get no quote."""
    price = options[price_column].to_numpy(float)
    volume = options["volume"].to_numpy(float)
    lots = volume / options["lot_size"].to_numpy(float)
    moneyness = np.abs(options["strike"].to_numpy(float) / spot - 1) * 100
    half = np.maximum(
        price * spread_percent(moneyness, lots, config) / 200 * multiplier, config.tick
    )
    traded = (price > 0) & (volume > 0)
    bid = np.where(traded & (price - half > 0), price - half, np.nan)
    ask = np.where(traded, price + half, np.nan)
    depth = np.floor(volume * config.depth_share)
    return pd.DataFrame(
        {
            "ts": now,
            "underlying_key": symbol,
            "expiry": options["expiry"].to_numpy(),
            "strike": options["strike"].to_numpy(float),
            "option_type": options["option_type"].to_numpy(),
            "instrument_key": contract_key(symbol, options).to_numpy(),
            "spot": spot,
            "ltp": price,
            "close_price": options["prev_close"].to_numpy(float),
            "bid": bid,
            "ask": ask,
            "bid_qty": depth,
            "ask_qty": depth,
            "volume": volume,
            "oi": options["oi"].to_numpy(float),
            "prev_oi": (options["oi"] - options["oi_change"]).to_numpy(float),
            "broker_iv": np.nan,
            "broker_delta": np.nan,
            "broker_gamma": np.nan,
            "broker_theta": np.nan,
            "broker_vega": np.nan,
            "broker_pop": np.nan,
        },
        columns=CHAIN_COLUMNS,
    )


def fit_spread_model(snapshots: pd.DataFrame, lot_size: float) -> dict[str, float] | None:
    """Least-squares spread parameters from real quotes, or None without enough data."""
    usable = snapshots[(snapshots["bid"] > 0) & (snapshots["ask"] > snapshots["bid"])]
    if len(usable) < 100:
        return None
    mid = (usable["bid"] + usable["ask"]) / 2
    target = ((usable["ask"] - usable["bid"]) / mid * 100).to_numpy(float)
    design = np.column_stack(
        [
            np.ones(len(usable)),
            (usable["strike"] / usable["spot"] - 1).abs().to_numpy(float) * 100,
            1 / np.sqrt(1 + usable["volume"].to_numpy(float) / lot_size),
        ]
    )
    solution, *_ = np.linalg.lstsq(design, target, rcond=None)
    base, moneyness, illiquidity = np.maximum(solution, 0.0)
    return {
        "base_pct": float(base),
        "moneyness_pct_per_pct": float(moneyness),
        "illiquidity_pct": float(illiquidity),
        "observations": float(len(usable)),
    }

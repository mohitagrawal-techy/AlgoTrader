from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest
from algoverse.analytics.chain import years_to_expiry
from algoverse.analytics.pricing import black76_price
from algoverse.evaluation.config import load_scoring_config
from algoverse.risk.config import load_risk_config
from algoverse.risk.costs import load_cost_model

IST = ZoneInfo("Asia/Kolkata")
CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
NOW = datetime(2026, 11, 12, 10, 0, tzinfo=IST)
EXPIRIES = (date(2026, 11, 17), date(2026, 11, 24), date(2026, 12, 29))


def make_raw_chain(now=NOW, spot=24000.0, sigma=0.14, r=0.055, expiries=EXPIRIES, spread=0.6):
    """A liquid, arbitrage-free chain in the shape returned by the broker client."""
    rows = []
    for expiry in expiries:
        T = years_to_expiry(expiry, now)
        forward = spot * np.exp(r * T)
        strikes = np.arange(22000.0, 26001.0, 50.0)
        for option_type, is_call in (("CE", True), ("PE", False)):
            mids = np.maximum(black76_price(forward, strikes, T, sigma, r, is_call), 0.5)
            for strike, mid in zip(strikes, mids, strict=True):
                rows.append(
                    {
                        "ts": now,
                        "underlying_key": "NSE_INDEX|Nifty 50",
                        "expiry": expiry,
                        "strike": float(strike),
                        "option_type": option_type,
                        "instrument_key": f"{expiry}|{strike:g}|{option_type}",
                        "spot": spot,
                        "ltp": mid,
                        "close_price": mid * 0.98,
                        "bid": max(mid - spread / 2, 0.05),
                        "ask": mid + spread / 2,
                        "bid_qty": 6500.0,
                        "ask_qty": 6500.0,
                        "volume": 500000.0,
                        "oi": 200000.0 + abs(strike - spot) * 10,
                        "prev_oi": 190000.0,
                    }
                )
    return pd.DataFrame(rows)


def make_contracts(chain):
    frame = chain[["instrument_key"]].drop_duplicates().copy()
    frame["lot_size"] = 65
    return frame


def make_bars(end=date(2026, 11, 11), count=300, start=20000.0, drift=10.0, seed=3):
    """Daily bars ending at `end`, with a steady uptrend by default."""
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(drift + rng.normal(0, 40, count))
    dates = pd.bdate_range(end=pd.Timestamp(end), periods=count, tz="Asia/Kolkata")
    return pd.DataFrame(
        {
            "ts": dates,
            "open": close - 10,
            "high": close + 60,
            "low": close - 60,
            "close": close,
            "volume": 1000.0,
            "oi": 0.0,
        }
    )


def later(minutes):
    return NOW + timedelta(minutes=minutes)


@pytest.fixture
def risk_config():
    return load_risk_config(CONFIG_DIR)


@pytest.fixture
def scoring_config():
    return load_scoring_config(CONFIG_DIR)


@pytest.fixture
def cost_model():
    return load_cost_model(CONFIG_DIR)

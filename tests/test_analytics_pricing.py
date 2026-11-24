from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest
from algoverse.analytics.chain import enrich_chain, implied_forward, mid_prices, years_to_expiry
from algoverse.analytics.pricing import black76_greeks, black76_price, implied_vol

IST = ZoneInfo("Asia/Kolkata")


def test_black76_known_value():
    assert black76_price(100, 100, 1, 0.2, 0, True) == pytest.approx(7.965567, abs=1e-5)


def test_discounting():
    undiscounted = black76_price(100, 90, 0.5, 0.25, 0, False)
    assert black76_price(100, 90, 0.5, 0.25, 0.07, False) == pytest.approx(
        undiscounted * np.exp(-0.035)
    )


def test_put_call_parity():
    strikes = np.array([80.0, 100.0, 125.0])
    call = black76_price(100, strikes, 0.25, 0.3, 0.06, True)
    put = black76_price(100, strikes, 0.25, 0.3, 0.06, False)
    assert call - put == pytest.approx(np.exp(-0.015) * (100 - strikes))


def test_expired_option_is_intrinsic():
    assert black76_price(110, 100, 0, 0.2, 0.05, True) == pytest.approx(10)
    assert black76_price(110, 100, -0.01, 0.2, 0.05, False) == pytest.approx(0)


def test_iv_round_trip():
    strikes = np.linspace(80, 120, 9)
    sigma = np.linspace(0.12, 0.45, 9)
    is_call = strikes > 100
    prices = black76_price(100, strikes, 0.1, sigma, 0.06, is_call)
    assert implied_vol(prices, 100, strikes, 0.1, 0.06, is_call) == pytest.approx(sigma, abs=1e-6)


def test_iv_rejects_impossible_prices():
    below_intrinsic = implied_vol(5.0, 110, 100, 0.1, 0.0, True)
    above_maximum = implied_vol(200.0, 110, 100, 0.1, 0.0, True)
    expired = implied_vol(10.0, 110, 100, 0.0, 0.0, True)
    missing = implied_vol(np.nan, 110, 100, 0.1, 0.0, True)
    assert np.isnan([below_intrinsic, above_maximum, expired, missing]).all()


@pytest.mark.parametrize("is_call", [True, False])
def test_greeks_match_finite_differences(is_call):
    F, K, T, sigma, r, spot = 24100.0, 24000.0, 20 / 365, 0.14, 0.065, 24000.0
    greeks = black76_greeks(F, K, T, sigma, r, is_call, spot=spot)
    carry = F / spot

    def by_spot(s):
        return float(black76_price(s * carry, K, T, sigma, r, is_call))

    h = 1.0
    assert greeks.delta == pytest.approx(
        (by_spot(spot + h) - by_spot(spot - h)) / (2 * h), rel=1e-4
    )
    assert greeks.gamma == pytest.approx(
        (by_spot(spot + h) - 2 * by_spot(spot) + by_spot(spot - h)) / h**2, rel=1e-3
    )
    vega = black76_price(F, K, T, sigma + 0.0001, r, is_call) - black76_price(
        F, K, T, sigma - 0.0001, r, is_call
    )
    assert greeks.vega == pytest.approx(float(vega) / 0.0002 / 100, rel=1e-4)
    dt = 1 / 365
    theta = black76_price(F, K, T - dt / 100, sigma, r, is_call) - black76_price(
        F, K, T + dt / 100, sigma, r, is_call
    )
    assert greeks.theta == pytest.approx(float(theta) / (2 * dt / 100) / 365, rel=1e-3)


def test_put_delta_is_negative():
    assert black76_greeks(100, 100, 0.1, 0.2, 0.05, False).delta < 0


NOW = datetime(2026, 11, 12, 10, 0, tzinfo=IST)
EXPIRY = date(2026, 11, 17)


def synthetic_chain(forward=24120.0, sigma=0.15, r=0.065, spread=1.0):
    strikes = np.arange(23500.0, 24801.0, 100.0)
    T = years_to_expiry(EXPIRY, NOW)
    rows = []
    for option_type, is_call in (("CE", True), ("PE", False)):
        mids = black76_price(forward, strikes, T, sigma, r, is_call)
        for strike, mid in zip(strikes, mids, strict=True):
            rows.append(
                {
                    "expiry": EXPIRY,
                    "strike": strike,
                    "option_type": option_type,
                    "spot": 24100.0,
                    "bid": mid - spread / 2,
                    "ask": mid + spread / 2,
                }
            )
    return pd.DataFrame(rows)


def test_expiry_time_uses_market_close():
    assert years_to_expiry(EXPIRY, datetime(2026, 11, 16, 15, 30, tzinfo=IST)) == pytest.approx(
        1 / 365
    )
    assert years_to_expiry(EXPIRY, datetime(2026, 11, 17, 15, 30, tzinfo=IST)) == 0


def test_implied_forward_recovers_truth():
    chain = synthetic_chain()
    forward = implied_forward(chain, 24100.0, years_to_expiry(EXPIRY, NOW), 0.065)
    assert forward == pytest.approx(24120.0, abs=0.01)


def test_enrich_chain_recovers_iv_and_delta_bounds():
    enriched = enrich_chain(synthetic_chain(), NOW, 0.065)
    assert enriched["forward_implied"].all()
    near = enriched[(enriched["strike"] - 24120.0).abs() <= 300]
    assert near["iv"].to_numpy() == pytest.approx(0.15, abs=1e-3)
    calls = enriched[enriched["option_type"] == "CE"]
    assert calls["delta"].between(0, 1).all()
    assert calls.sort_values("strike")["delta"].is_monotonic_decreasing


def test_missing_quotes_never_get_an_iv():
    chain = synthetic_chain()
    chain.loc[0, "bid"] = 0.0
    chain.loc[1, ["bid", "ask"]] = [50.0, 40.0]
    assert mid_prices(chain).iloc[:2].isna().all()
    assert enrich_chain(chain, NOW, 0.065)["iv"].iloc[:2].isna().all()


def test_forward_falls_back_and_is_flagged_without_two_sided_quotes():
    chain = synthetic_chain()
    chain["bid"] = 0.0
    enriched = enrich_chain(chain, NOW, 0.065)
    assert not enriched["forward_implied"].any()
    T = years_to_expiry(EXPIRY, NOW)
    assert enriched["forward"].iloc[0] == pytest.approx(24100.0 * np.exp(0.065 * T))


def test_chain_after_expiry_has_no_iv():
    enriched = enrich_chain(synthetic_chain(), NOW + timedelta(days=10), 0.065)
    assert enriched["iv"].isna().all()

from datetime import date

import numpy as np
import pandas as pd
import pytest
from algoverse.analytics.iv import atm_iv, iv_ranking, skew_25d, term_slope, term_structure
from algoverse.analytics.levels import (
    Level,
    camarilla_pivots,
    classic_pivots,
    confirmed_swings,
    confluence_zones,
    session_vwap,
)
from algoverse.analytics.positioning import classify_buildup, max_pain, oi_walls, put_call_ratio
from algoverse.analytics.regime import adx, realised_vol, regime_snapshot


def test_iv_ranking_values():
    history = pd.Series(np.arange(10.0, 51.0))
    result = iv_ranking(history, 20.0)
    assert result.rank == pytest.approx(25.0)
    assert result.percentile == pytest.approx(10 / 41 * 100)
    assert result.sample_size == 41


def test_iv_ranking_requires_enough_history():
    assert iv_ranking(pd.Series([10.0] * 29), 12.0) is None


def test_iv_ranking_clips_new_extremes():
    assert iv_ranking(pd.Series(np.arange(40.0)), 100.0).rank == 100.0


def expiry_slice(expiry, T, forward, atm):
    return pd.DataFrame(
        {
            "expiry": expiry,
            "T": T,
            "forward": forward,
            "strike": [99.0, 99.0, 101.0, 101.0],
            "option_type": ["CE", "PE", "CE", "PE"],
            "iv": [atm, atm, atm + 0.1, atm + 0.1],
        }
    )


def test_atm_iv_and_term_structure():
    near = expiry_slice(date(2026, 11, 17), 0.02, 99.5, 0.20)
    far = expiry_slice(date(2026, 12, 29), 0.13, 99.5, 0.15)
    assert atm_iv(near) == pytest.approx(0.20)
    structure = term_structure(pd.concat([far, near]))
    assert list(structure["atm_iv"]) == pytest.approx([0.20, 0.15])
    assert term_slope(structure) == pytest.approx(-0.05)


def test_skew_interpolates_at_25_delta():
    chain = pd.DataFrame(
        {
            "option_type": ["CE", "CE", "PE", "PE"],
            "delta": [0.20, 0.30, -0.30, -0.20],
            "iv": [0.12, 0.14, 0.20, 0.18],
        }
    )
    assert skew_25d(chain) == pytest.approx(0.19 - 0.13)


def test_skew_is_none_when_25_delta_is_not_bracketed():
    chain = pd.DataFrame({"option_type": ["CE", "PE"], "delta": [0.40, -0.40], "iv": [0.12, 0.2]})
    assert skew_25d(chain) is None


def oi_chain():
    return pd.DataFrame(
        {
            "strike": [100.0, 100.0, 110.0, 110.0, 120.0, 120.0],
            "option_type": ["CE", "PE"] * 3,
            "oi": [10.0, 50.0, 30.0, 20.0, 60.0, 5.0],
        }
    )


def test_pcr():
    assert put_call_ratio(oi_chain()) == pytest.approx(75 / 100)


def test_max_pain_matches_hand_calculation():
    # Settle 100: calls 0, puts 10*20 + 20*5 = 300.
    # Settle 110: calls 10*10 = 100, puts 10*5 = 50, total 150.
    # Settle 120: calls 20*10 + 10*30 = 500, puts 0.
    assert max_pain(oi_chain()) == 110.0


def test_oi_walls():
    assert oi_walls(oi_chain(), count=1) == {"resistance": [120.0], "support": [100.0]}


def test_buildup_labels():
    chain = pd.DataFrame(
        {
            "ltp": [11.0, 9.0, 11.0, 9.0, 10.0],
            "close_price": [10.0] * 5,
            "oi": [20.0, 20.0, 5.0, 5.0, 20.0],
            "prev_oi": [10.0] * 5,
        }
    )
    assert list(classify_buildup(chain)) == [
        "long_buildup",
        "short_buildup",
        "short_covering",
        "long_unwinding",
        "neutral",
    ]


def bars(count=120, drift=1.0, seed=7):
    rng = np.random.default_rng(seed)
    close = pd.Series(1000 + np.cumsum(drift + rng.normal(0, 0.5, count)))
    return pd.DataFrame(
        {"open": close - 0.2, "high": close + 1.0, "low": close - 1.0, "close": close}
    )


def test_realised_vol_of_alternating_returns():
    close = pd.Series(100 * np.exp(np.cumsum([0.01, -0.01] * 30)))
    expected = np.std([0.01, -0.01] * 10, ddof=1) * np.sqrt(252)
    assert realised_vol(close).iloc[-1] == pytest.approx(expected, rel=0.01)


def test_adx_is_high_in_a_steady_trend_and_low_in_chop():
    rng = np.random.default_rng(1)
    close = pd.Series(1000 + rng.normal(0, 3, 300))
    chop = pd.DataFrame({"high": close + 2, "low": close - 2, "close": close})
    assert adx(bars(300, drift=5.0)).iloc[-1] > 50
    assert adx(chop).iloc[-1] < 30


def test_regime_detects_direction():
    assert regime_snapshot(bars(drift=5.0)).trend == "up"
    assert regime_snapshot(bars(drift=-5.0)).trend == "down"


def test_regime_needs_history():
    assert regime_snapshot(bars(30)) is None


def test_regime_indicators_ignore_future_bars():
    frame = bars(200)
    changed = frame.copy()
    changed.iloc[150:, :] *= 3
    pd.testing.assert_series_equal(adx(frame).iloc[:150], adx(changed).iloc[:150])
    pd.testing.assert_series_equal(
        realised_vol(frame["close"]).iloc[:150], realised_vol(changed["close"]).iloc[:150]
    )


def test_pivots():
    pivots = classic_pivots(110.0, 90.0, 100.0)
    assert pivots["P"] == 100.0
    assert (pivots["R1"], pivots["S1"], pivots["R2"], pivots["S2"]) == (110, 90, 120, 80)
    assert (pivots["R3"], pivots["S3"]) == (130, 70)
    assert camarilla_pivots(110.0, 90.0, 100.0)["R3"] == pytest.approx(105.5)


def test_swings_appear_only_after_confirmation():
    high = [10.0, 11.0, 12.0, 20.0, 12.0, 11.0, 10.0, 9.0, 8.0]
    frame = pd.DataFrame({"high": high, "low": [h - 1 for h in high]})
    swings = confirmed_swings(frame, n=2)
    assert swings["swing_high"].iloc[:5].isna().all()
    assert swings["swing_high"].iloc[5] == 20.0


def test_swings_ignore_bars_after_confirmation():
    high = [10.0, 11.0, 12.0, 20.0, 12.0, 11.0, 10.0, 9.0, 8.0]
    frame = pd.DataFrame({"high": high, "low": [h - 1 for h in high]})
    changed = frame.copy()
    changed.iloc[7:, :] = 500.0
    swings = confirmed_swings(frame, n=2)["swing_high"].iloc[:6]
    assert swings.equals(confirmed_swings(changed, n=2)["swing_high"].iloc[:6])


def test_vwap_resets_each_session():
    index = pd.to_datetime(["2026-11-12 09:15", "2026-11-12 09:20", "2026-11-13 09:15"])
    frame = pd.DataFrame(
        {
            "high": [100.0, 110.0, 200.0],
            "low": [100.0, 110.0, 200.0],
            "close": [100.0, 110.0, 200.0],
            "volume": [1.0, 3.0, 5.0],
        },
        index=index,
    )
    assert list(session_vwap(frame)) == pytest.approx([100.0, 107.5, 200.0])


def test_confluence_ranks_by_distinct_methods():
    zones = confluence_zones(
        [
            Level(24000.0, "max_call_oi"),
            Level(24010.0, "pivot"),
            Level(24015.0, "pivot"),
            Level(24500.0, "swing"),
        ]
    )
    assert zones[0].methods == ("max_call_oi", "pivot")
    assert zones[0].strength == 2
    assert (zones[0].low, zones[0].high) == (24000.0, 24015.0)
    assert zones[1].center == 24500.0
    assert confluence_zones([]) == []

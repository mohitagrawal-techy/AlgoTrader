import numpy as np
import pandas as pd
import pytest
from algoverse.lab.technical import INDICATORS, PATTERNS, Frames, Operand, catalog
from conftest import IST
from test_technical_rules import candles


def intraday(sessions=4, minutes=15, seed=5):
    """`minutes` candles for a few sessions, 09:15 to 15:30."""
    rng = np.random.default_rng(seed)
    stamps = []
    for day in pd.bdate_range("2026-03-02", periods=sessions):
        start = pd.Timestamp(day).tz_localize(IST) + pd.Timedelta(hours=9, minutes=15)
        stamps += [start + pd.Timedelta(minutes=minutes * i) for i in range(375 // minutes)]
    closes = 22000 + np.cumsum(rng.normal(0, 15, len(stamps)))
    opens = closes - rng.normal(0, 8, len(stamps))
    return pd.DataFrame(
        {
            "ts": stamps,
            "open": opens,
            "high": np.maximum(opens, closes) + rng.uniform(1, 10, len(stamps)),
            "low": np.minimum(opens, closes) - rng.uniform(1, 10, len(stamps)),
            "close": closes,
        }
    )


def compute(name, bars, field=None):
    indicator = INDICATORS[name]
    params = {p.name: p.default for p in indicator.params}
    result = indicator.compute(bars, **params)
    return result[field] if isinstance(result, dict) else result


def daily(n=300, seed=3):
    rng = np.random.default_rng(seed)
    closes = 20000 + np.cumsum(rng.normal(5, 80, n))
    return candles(closes, opens=closes - rng.normal(0, 40, n))


@pytest.mark.parametrize("name", sorted(INDICATORS))
def test_no_indicator_reads_later_candles(name):
    """Values up to a candle must not change when later candles are added or altered."""
    indicator = INDICATORS[name]
    bars = intraday() if indicator.intraday_only else daily()
    cut = len(bars) * 2 // 3
    altered = bars.copy()
    altered.loc[cut:, ["open", "high", "low", "close"]] *= 1.07
    for field in list(indicator.fields) or [None]:
        early = compute(name, bars.iloc[:cut].reset_index(drop=True), field).to_numpy(float)
        full = compute(name, altered, field).to_numpy(float)[:cut]
        assert np.allclose(early, full, equal_nan=True), (name, field)


def test_day_levels_on_intraday_candles_come_from_the_previous_session():
    bars = intraday(sessions=2)
    first = bars[bars["ts"].dt.date == bars["ts"].dt.date.iloc[0]]
    second = bars.index[bars["ts"].dt.date != bars["ts"].dt.date.iloc[0]]
    high, low, close = first["high"].max(), first["low"].min(), first["close"].iloc[-1]
    pivot = (high + low + close) / 3
    assert compute("cpr", bars, "p")[second].eq(pivot).all()
    assert compute("cpr", bars, "p")[first.index].isna().all()
    assert compute("prev_day", bars, "high")[second].eq(high).all()
    today_high = compute("today", bars, "high")
    assert today_high[second[0]] == bars.loc[second[0], "high"]
    assert today_high[second[-1]] == bars.loc[second, "high"].max()


def test_candle_at_a_time_is_known_from_that_candle_on():
    bars = intraday(sessions=1)
    params = {"at": 930}
    at_0930 = INDICATORS["candle_at"].compute(bars, **params)["close"]
    row = bars.index[bars["ts"].dt.strftime("%H:%M") == "09:30"][0]
    assert at_0930[:row].isna().all()
    assert at_0930[row:].eq(bars.loc[row, "close"]).all()
    # On 15-minute candles, 09:20 falls in the 09:15 candle.
    within = INDICATORS["candle_at"].compute(bars, at=920)["close"]
    first = bars.index[0]
    assert within[first:].eq(bars.loc[first, "close"]).all()
    # Daily candles have no time of day.
    assert INDICATORS["candle_at"].compute(daily(20), **params)["close"].isna().all()


def test_trend_followers_turn_with_the_trend():
    rise = candles(list(np.linspace(100, 200, 60)) + list(np.linspace(200, 120, 40)))
    psar = compute("psar", rise, "direction")
    assert psar.iloc[55] == 1 and psar.iloc[-1] == -1
    half = compute("halftrend", rise, "direction")
    assert half.iloc[55] == 1 and half.iloc[-1] == -1
    aroon = compute("aroon", rise.iloc[:60], "up")
    assert aroon.iloc[-1] == 100


def test_ichimoku_cloud_is_drawn_from_earlier_candles():
    bars = daily(120)
    cloud = INDICATORS["ichimoku"].compute(bars, conversion=9, base=26, span=52)
    tenkan, kijun = cloud["conversion"], cloud["base"]
    assert cloud["span_a"].iloc[-1] == pytest.approx((tenkan.iloc[-27] + kijun.iloc[-27]) / 2)


@pytest.mark.parametrize(
    ("pattern", "opens", "closes", "highs", "lows"),
    [
        ("dark_cloud_cover", [100, 100, 108], [100, 107, 102], [101, 108, 109], [99, 99, 101]),
        ("piercing_line", [100, 108, 99], [100, 101, 106], [101, 109, 107], [99, 100, 98]),
        ("dragonfly_doji", [100, 100, 100], [100, 101, 100.05], [101, 102, 100.1], [99, 99, 95]),
        ("gravestone_doji", [100, 100, 100], [100, 101, 100.05], [101, 102, 105], [99, 99, 99.95]),
        ("long_legged_doji", [100, 100, 100], [100, 101, 100.05], [101, 102, 103], [99, 99, 97]),
        ("bullish_spinning_top", [100, 100, 100], [100, 101, 101], [101, 102, 103], [99, 99, 98]),
        ("bearish_spinning_top", [100, 100, 101], [100, 101, 100], [101, 102, 103], [99, 99, 98]),
        ("strong_green", [100, 100, 100], [100, 101, 104], [101, 102, 105], [99, 99, 100]),
        ("strong_red", [100, 100, 104], [100, 101, 100], [101, 102, 104], [99, 99, 99]),
    ],
)
def test_new_candle_patterns(pattern, opens, closes, highs, lows):
    bars = candles(closes, opens, highs, lows)
    assert PATTERNS[pattern].detect(bars).iloc[-1]


def test_hammers_need_the_move_before_them():
    rise = [100, 102, 104, 106, 108]
    hammer = dict(opens=rise + [108], closes=rise + [107.5], highs=[r + 1 for r in rise] + [108.2])
    lows = [r - 1 for r in rise] + [104]
    bars = candles(hammer["closes"], hammer["opens"], hammer["highs"], lows)
    assert PATTERNS["hanging_man"].detect(bars).iloc[-1]
    assert not PATTERNS["inverted_hammer"].detect(bars).iloc[-1]


def test_new_indicators_are_offered_and_readable_in_rules():
    names = {item["name"] for item in catalog()["indicators"]}
    assert {"hma", "keltner", "ichimoku", "psar", "cpr", "qqe", "candle_at"} <= names
    frames = Frames(daily())
    value = frames.value("1d", Operand(name="cpr", field="tc"))
    assert value is not None and np.isfinite(value)


def test_every_indicator_has_one_type():
    from algoverse.lab.technical import INDICATOR_GROUPS

    listed = [name for names in INDICATOR_GROUPS.values() for name in names]
    assert sorted(listed) == sorted(INDICATORS) and len(listed) == len(set(listed))
    offered = catalog()
    names = [group["name"] for group in offered["indicator_groups"]]
    assert names[:2] == ["Price", "Moving averages"]
    assert all(item["group"] != "Other" for item in offered["indicators"])


def test_every_indicator_explains_itself():
    for item in catalog()["indicators"]:
        assert item["description"] and item["formula"], item["name"]
        assert "\n" not in item["description"], item["name"]


def test_session_average_resets_each_day_and_skips_daily_candles():
    from algoverse.lab.technical import _session_average

    times = pd.to_datetime(
        ["2026-01-05 09:15", "2026-01-05 09:20", "2026-01-06 09:15", "2026-01-06 09:20"]
    ).tz_localize("Asia/Kolkata")
    bars = pd.DataFrame(
        {
            "ts": times,
            "open": [100.0, 110, 200, 210],
            "high": [100.0, 110, 200, 210],
            "low": [100.0, 110, 200, 210],
            "close": [100.0, 110, 200, 210],
        }
    )
    values = _session_average(bars, 2.0)
    assert list(values["avg"]) == [100.0, 105.0, 200.0, 205.0]
    assert values["dev_pct"].iloc[1] == pytest.approx((110 / 105 - 1) * 100)
    daily = bars.assign(ts=pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]))
    assert _session_average(daily, 2.0)["avg"].isna().all()

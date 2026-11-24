from datetime import datetime

import pandas as pd
from algoverse.brokers.base import MarketDataError
from algoverse.data.candles import MINUTE
from algoverse.data.store import SnapshotStore
from algoverse.execution.live_candles import LiveCandles
from conftest import IST

NIFTY = "NSE_INDEX|Nifty 50"


def minutes(day, start, count, base=100.0):
    ts = pd.date_range(f"{day} {start}", periods=count, freq="min", tz=IST)
    values = base + pd.Series(range(count), dtype=float)
    return pd.DataFrame(
        {
            "ts": ts,
            "open": values,
            "high": values + 1,
            "low": values - 1,
            "close": values,
            "volume": 0,
            "oi": 0,
        }
    )


class Market:
    def __init__(self, frame=None, fail=False):
        self.frame, self.fail, self.calls = frame, fail, 0

    def intraday_candles(self, key, minutes_):
        self.calls += 1
        if self.fail:
            raise MarketDataError("down")
        return self.frame


def test_history_and_today_join_and_unfinished_minutes_are_left_out(tmp_path):
    store = SnapshotStore(tmp_path)
    store.candles.write(NIFTY, minutes("2026-11-11", "09:15", 375), MINUTE)
    market = Market(minutes("2026-11-12", "09:15", 31, base=200.0))
    now = datetime(2026, 11, 12, 9, 45, 30, tzinfo=IST)
    live = LiveCandles(market, store, clock=lambda: now, monotonic=lambda: 0.0)
    frame = live.minutes("NIFTY")
    assert len(frame) == 375 + 30  # 09:45 has not finished at 09:45:30.
    assert frame["ts"].iloc[-1].strftime("%H:%M") == "09:44"
    bar = live.today_bar("NIFTY")
    assert bar["open"] == 200.0 and bar["close"] == 230.0
    assert market.calls == 1  # Cached within the minute.


def test_upstox_failure_means_no_candles_not_a_crash(tmp_path):
    live = LiveCandles(
        Market(fail=True),
        SnapshotStore(tmp_path),
        clock=lambda: datetime(2026, 11, 12, 10, 0, tzinfo=IST),
    )
    assert live.minutes("NIFTY").empty and live.today_bar("NIFTY") is None

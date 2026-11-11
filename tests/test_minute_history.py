from datetime import date

import pandas as pd
import pytest
from algoverse.brokers.base import MarketDataError
from algoverse.data.candles import MINUTE, CandleStore
from algoverse.data.minute_history import backfill_minutes, months

KEY = "NSE_INDEX|Nifty 50"


class FakeMarket:
    def __init__(self, fail_month=None):
        self.calls = []
        self.fail_month = fail_month

    def minute_range(self, key, start, end):
        self.calls.append((key, start, end))
        if self.fail_month and start.month == self.fail_month:
            raise MarketDataError("boom")
        days = pd.bdate_range(start, end)
        ts = [
            pd.Timestamp(f"{day.date()} 09:15").tz_localize("Asia/Kolkata")
            + pd.Timedelta(minutes=m)
            for day in days
            for m in range(3)
        ]
        return pd.DataFrame(
            {"ts": ts, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0, "oi": 0}
        )


def weekdays(first, last):
    return len(pd.bdate_range(first, last))


def test_months_are_clipped_to_the_span():
    assert months(date(2025, 1, 20), date(2025, 3, 5)) == [
        (date(2025, 1, 20), date(2025, 1, 31)),
        (date(2025, 2, 1), date(2025, 2, 28)),
        (date(2025, 3, 1), date(2025, 3, 5)),
    ]


def test_backfill_stores_months_and_skips_complete_ones_on_a_rerun(tmp_path):
    store = CandleStore(tmp_path)
    market = FakeMarket()
    result = backfill_minutes(
        market, store, {"NIFTY": KEY}, date(2025, 1, 1), date(2025, 3, 1), weekdays
    )
    assert result.fetched["NIFTY"] == 2 and len(market.calls) == 2
    held = store.read(KEY, MINUTE)
    assert len(held) == 3 * weekdays(date(2025, 1, 1), date(2025, 2, 28))
    again = FakeMarket()
    rerun = backfill_minutes(
        again, store, {"NIFTY": KEY}, date(2025, 1, 1), date(2025, 3, 1), weekdays
    )
    assert again.calls == [] and rerun.kept["NIFTY"] == 2


def test_a_failed_month_is_reported_and_the_rest_continue(tmp_path):
    market = FakeMarket(fail_month=1)
    result = backfill_minutes(
        market, CandleStore(tmp_path), {"NIFTY": KEY}, date(2025, 1, 1), date(2025, 3, 1), weekdays
    )
    assert list(result.failed) == ["NIFTY 2025-01"] and result.fetched["NIFTY"] == 1


def test_today_is_never_fetched(tmp_path):
    market = FakeMarket()
    backfill_minutes(
        market, CandleStore(tmp_path), {"NIFTY": KEY}, date(2025, 2, 1), date(2025, 2, 10), weekdays
    )
    assert market.calls[-1][2] == date(2025, 2, 9)


@pytest.mark.parametrize("span", [(date(2025, 2, 1), date(2025, 2, 28))])
def test_months_cover_whole_month(span):
    assert months(*span) == [span]

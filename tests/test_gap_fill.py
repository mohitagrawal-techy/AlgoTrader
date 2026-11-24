from datetime import date

import pandas as pd
import pytest
from algoverse.backtest.bhavcopy import COLUMNS, store_day
from algoverse.backtest.config import load_backtest_config
from algoverse.backtest.dataview import History
from algoverse.backtest.upstox_gaps import fill_gaps
from algoverse.backtest.upstox_history import _save_written
from algoverse.brokers.base import MarketDataError
from algoverse.lab.evaluate import normalize_reason
from conftest import CONFIG_DIR, IST

TODAY = date(2026, 11, 12)
DAYS = [date(2026, 10, 27), date(2026, 10, 28), date(2026, 10, 29), date(2026, 11, 11)]
LIVE = date(2026, 11, 17)
FUTURE = date(2026, 11, 24)


def session_rows(day, expiries):
    rows = [
        {
            "date": day,
            "symbol": "NIFTY",
            "instrument": "IDX",
            "expiry": day,
            "strike": float("nan"),
            "option_type": None,
            "open": 22700.0,
            "high": 22800.0,
            "low": 22600.0,
            "close": 22750.0,
            "settle": 22750.0,
            "volume": 0.0,
            "oi": 0.0,
            "oi_change": 0.0,
            "lot_size": 0,
            "underlying_price": 22750.0,
        }
    ]
    for expiry in expiries:
        rows.append(
            rows[0]
            | {
                "instrument": "OPT",
                "expiry": expiry,
                "strike": 22700.0,
                "option_type": "CE",
                "close": 120.0,
                "lot_size": 65,
            }
        )
    frame = pd.DataFrame(rows)
    frame["option_type"] = frame["option_type"].astype(object)
    return frame[COLUMNS]


class Market:
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.minute_calls = 0

    def daily_candles(self, key, start, end):
        days = [d for d in DAYS if start <= d <= end]
        base = 22000.0 if key == "NSE_INDEX|Nifty 50" else 22100.0
        return pd.DataFrame(
            {
                "ts": [pd.Timestamp(d).tz_localize(IST) for d in days],
                "open": base,
                "high": base + 100,
                "low": base - 100,
                "close": [base + 10 * i for i in range(len(days))],
                "volume": 10.0,
                "oi": 5.0,
            }
        )

    def minute_range(self, key, start, end):
        self.minute_calls += 1
        if key in self.fail:
            raise MarketDataError("refused")
        rows = []
        for day in DAYS:
            if not start <= day <= end:
                continue
            first = pd.Timestamp(day).tz_localize(IST) + pd.Timedelta(hours=9, minutes=15)
            for minute in range(375):
                price = 100.0 + minute / 10
                rows.append(
                    {
                        "ts": first + pd.Timedelta(minutes=minute),
                        "open": price,
                        "high": price + 1,
                        "low": price - 1,
                        "close": price + 0.5,
                        "volume": 2.0,
                        "oi": 1000.0 + minute,
                    }
                )
        return pd.DataFrame(rows)


def contracts():
    return pd.DataFrame(
        [
            {
                "underlying_symbol": "NIFTY",
                "underlying_key": "NSE_INDEX|Nifty 50",
                "instrument_key": f"NSE_FO|{strike}{kind}",
                "expiry": LIVE,
                "strike": float(strike),
                "option_type": kind,
                "lot_size": 65,
            }
            for strike in (22700, 22800)
            for kind in ("CE", "PE")
        ]
    )


FUTURES = pd.DataFrame(
    [
        {
            "underlying_symbol": "NIFTY",
            "instrument_key": "NSE_FO|FUT",
            "expiry": FUTURE,
            "lot_size": 65,
        }
    ]
)


@pytest.fixture
def setup(tmp_path):
    # Imported sessions before the gap list the 3 Nov weekly, which has since expired.
    store_day(tmp_path, session_rows(DAYS[0], [DAYS[0], date(2026, 11, 3), LIVE]))
    # Saved after the close from live contracts, but without 5-minute bars.
    store_day(tmp_path, session_rows(DAYS[3], [LIVE]))
    _save_written(tmp_path, {DAYS[3].isoformat()})
    config = load_backtest_config(CONFIG_DIR).model_copy(
        update={"symbols": ["NIFTY"], "expiries_per_underlying": 3, "strike_range": 0.15}
    )
    return tmp_path, config


def test_fills_missed_sessions_and_guards_lost_expiries(setup):
    data_dir, config = setup
    market = Market()
    result = fill_gaps(market, data_dir, config, contracts(), FUTURES, TODAY)
    assert result.days == [DAYS[1], DAYS[2]] and result.written == [DAYS[1], DAYS[2]]
    assert result.incomplete == {
        "2026-10-28": {"NIFTY": ["2026-11-03"]},
        "2026-10-29": {"NIFTY": ["2026-11-03"]},
    }
    history = History(data_dir)
    assert history.sessions == DAYS
    day = history.day(DAYS[1])
    assert set(day["instrument"]) == {"IDX", "FUT", "OPT"}
    option = day[(day["instrument"] == "OPT") & (day["option_type"] == "CE")].iloc[0]
    assert option["open"] == 100.0 and option["close"] == pytest.approx(137.4 + 0.5)
    assert option["oi"] == 1374.0 and option["lot_size"] == 65
    # Daily bars are continuous across the gap: the future stands in on filled days.
    assert list(history.bars("NIFTY")["ts"].dt.date) == DAYS

    reason = history.entry_block(DAYS[1], "NIFTY")
    assert reason.startswith("data incomplete") and "2026-11-03" in reason
    assert normalize_reason(reason) == "data incomplete"
    assert history.entry_block(DAYS[3], "NIFTY") is None

    # 5-minute bars for the filled days and for the saved session that had none.
    assert result.intraday == [DAYS[1], DAYS[2], DAYS[3]]
    bars = history.intraday(DAYS[1])
    assert len(bars) == 4 * 75 and set(bars["expiry"]) == {LIVE}
    first = pd.Timestamp(bars["t"].min(), unit="ms", tz="UTC").tz_convert(IST)
    assert (first.hour, first.minute) == (9, 15)
    # Only contracts in that session's daily file: the saved session holds one CE.
    assert len(history.intraday(DAYS[3])) == 75

    # A second run finds nothing to do and fetches nothing.
    calls = market.minute_calls
    again = fill_gaps(market, data_dir, config, contracts(), FUTURES, TODAY)
    assert not again.days and not again.intraday and market.minute_calls == calls


def test_too_many_failures_write_nothing(setup):
    data_dir, config = setup
    keys = set(contracts()["instrument_key"])
    result = fill_gaps(Market(fail=keys), data_dir, config, contracts(), FUTURES, TODAY)
    assert "NIFTY" in result.failed and not result.written
    assert History(data_dir).sessions == [DAYS[0], DAYS[3]]

"""The replay's trade chart: one option contract's candles, 5-minute where imported, else
the session's daily candle."""

from datetime import date

import pandas as pd
from algoverse.api.app import create_app
from algoverse.backtest.bhavcopy import COLUMNS
from algoverse.lab.replay import option_candles
from algoverse.settings import Settings
from fastapi.testclient import TestClient

EXPIRY = date(2026, 11, 17)


def daily_row(day: date, strike: float, close: float) -> dict:
    row = dict.fromkeys(COLUMNS)
    row |= {
        "date": day,
        "symbol": "NIFTY",
        "instrument": "OPT",
        "expiry": EXPIRY,
        "strike": strike,
        "option_type": "CE",
        "open": close,
        "high": close + 5,
        "low": close - 5,
        "close": close,
        "settle": close,
        "volume": 1.0,
        "oi": 1.0,
        "oi_change": 0.0,
        "lot_size": 65,
        "underlying_price": 25000.0,
    }
    return row


def store(tmp_path):
    folder = tmp_path / "history" / "fo"
    folder.mkdir(parents=True)
    for day, close in ((date(2026, 11, 10), 100.0), (date(2026, 11, 11), 110.0)):
        rows = [daily_row(day, 25000.0, close), daily_row(day, 25100.0, close / 2)]
        pd.DataFrame(rows).to_parquet(folder / f"{day.isoformat()}.parquet")
    # 5-minute bars on 11 Nov only: 09:15 and 09:20 IST.
    start = pd.Timestamp("2026-11-11 09:15", tz="Asia/Kolkata")
    bars = pd.DataFrame(
        {
            "symbol": "NIFTY",
            "t": [int(start.timestamp() * 1000), int(start.timestamp() * 1000) + 300_000],
            "expiry": EXPIRY,
            "strike": [25000.0, 25000.0],
            "option_type": "CE",
            "open": [108.0, 111.0],
            "high": [112.0, 115.0],
            "low": [107.0, 110.0],
            "close": [111.0, 114.0],
            "volume": 1.0,
            "oi": 1.0,
        }
    )
    intraday = folder / "intraday" / "date=2026-11-11"
    intraday.mkdir(parents=True)
    bars.to_parquet(intraday / "nifty_upstox.parquet")


def wall(text: str) -> int:
    return int((pd.Timestamp(text) - pd.Timestamp(0)).total_seconds())


def test_five_minute_bars_where_imported_else_the_daily_candle(tmp_path):
    store(tmp_path)
    found = option_candles(
        tmp_path, "NIFTY", EXPIRY, 25000.0, "CE", date(2026, 11, 10), date(2026, 11, 11)
    )
    assert found["time"] == [
        wall("2026-11-10 00:00"),
        wall("2026-11-11 09:15"),
        wall("2026-11-11 09:20"),
    ]
    assert found["close"] == [100.0, 111.0, 114.0]
    only_daily = option_candles(
        tmp_path, "NIFTY", EXPIRY, 25100.0, "CE", date(2026, 11, 10), date(2026, 11, 10)
    )
    assert only_daily["interval"] == "1d" and only_daily["close"] == [50.0]


def test_api(tmp_path):
    store(tmp_path)
    client = TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path, ai_enabled=False)))
    query = "symbol=NIFTY&expiry=2026-11-17&strike=25000&option_type=CE"
    body = client.get(f"/api/lab/replay/option?{query}&start=2026-11-11&end=2026-11-11").json()
    assert body["interval"] == "5m" and body["close"] == [111.0, 114.0]
    bad = client.get(f"/api/lab/replay/option?{query}&start=2026-11-12&end=2026-11-11")
    assert bad.status_code == 400

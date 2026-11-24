import json
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from algoverse.backtest.candle_files import import_candle_files
from algoverse.backtest.dataview import History

IST = ZoneInfo("Asia/Kolkata")
SESSIONS = [date(2025, 1, 6) + timedelta(days=i) for i in range(30) if (i % 7) < 5]
EXPIRIES = [date(2025, 1, 9), date(2025, 1, 16), date(2025, 1, 23), date(2025, 1, 30)]
STRIKES = [23000 + 50 * i for i in range(12)]


def minutes(day: date, price: float, oi: float) -> list[dict]:
    rows = []
    for minute, (o, c) in enumerate([(price, price + 1), (price + 1, price + 2)]):
        rows.append(
            {
                "timestamp": f"{day}T09:{15 + minute:02d}:00+05:30",
                "open": o,
                "high": c + 0.5,
                "low": o - 0.5,
                "close": c,
                "volume": 75,
                "open_interest": oi + minute,
            }
        )
    return rows


def write_folder(root, expiries=EXPIRIES, contract_list=True, stray=False):
    for expiry in expiries:
        folder = root / "candles/1minute" / expiry.isoformat()
        folder.mkdir(parents=True)
        contracts = []
        for strike in STRIKES:
            for kind in ("CE", "PE"):
                days = [d for d in SESSIONS if d <= expiry and d >= expiry - timedelta(days=21)]
                rows = [r for d in days for r in minutes(d, 100.0, 1000.0)]
                if stray and strike == STRIKES[0] and kind == "CE":
                    rows = minutes(date(2023, 11, 30), 5.0, 0.0) + rows
                pd.DataFrame(rows).to_csv(folder / f"{strike}_{kind}.csv", index=False)
                contracts.append(
                    {
                        "underlying_symbol": "NIFTY",
                        "strike_price": float(strike),
                        "instrument_type": kind,
                        "lot_size": 75,
                    }
                )
        if contract_list:
            target = root / "expired_contracts" / f"{expiry}.json"
            target.parent.mkdir(exist_ok=True)
            target.write_text(json.dumps(contracts))


def index_candles(_symbol):
    ts = pd.to_datetime([d.isoformat() for d in SESSIONS]).tz_localize(IST)
    return pd.DataFrame(
        {"ts": ts, "open": 23200.0, "high": 23300.0, "low": 23100.0, "close": 23250.0}
    )


def run(root, data, **kwargs):
    return import_candle_files(root, data, "NIFTY", index_candles, 3, 0.15, **kwargs)


def test_minutes_become_daily_sessions_with_index_bars(tmp_path):
    root, data = tmp_path / "files", tmp_path / "data"
    write_folder(root)
    result = run(root, data)
    assert result.expiries == 4 and result.written
    history = History(data)
    first = history.day(history.sessions[0])
    option = first[first["instrument"] == "OPT"].iloc[0]
    # Two minutes: open of the first, close of the last, summed volume, last OI.
    assert (option["open"], option["close"], option["volume"], option["oi"]) == (
        100.0,
        102.0,
        150.0,
        1001.0,
    )
    assert option["lot_size"] == 75 and option["underlying_price"] == 23250.0
    assert history.bars("NIFTY")["close"].iloc[0] == 23250.0


def test_sessions_without_three_expiries_are_skipped(tmp_path):
    root, data = tmp_path / "files", tmp_path / "data"
    write_folder(root)
    result = run(root, data)
    # From 17 Jan only two expiries in the files remain ahead, so later days are incomplete.
    assert max(result.written) <= EXPIRIES[1]
    assert result.incomplete and min(result.incomplete) > EXPIRIES[1]


def test_stray_bars_of_an_older_contract_are_dropped(tmp_path):
    root, data = tmp_path / "files", tmp_path / "data"
    write_folder(root, stray=True)
    run(root, data)
    history = History(data)
    assert min(history.sessions) >= date(2024, 12, 1)


def test_expiries_without_lot_sizes_are_reported(tmp_path):
    root, data = tmp_path / "files", tmp_path / "data"
    write_folder(root, contract_list=False)
    result = run(root, data)
    assert result.written == [] and len(result.problems) == 4


def test_dry_run_writes_nothing(tmp_path):
    root, data = tmp_path / "files", tmp_path / "data"
    write_folder(root)
    assert run(root, data, dry_run=True).written
    assert History(data).sessions == []


def test_missing_folder_is_refused(tmp_path):
    with pytest.raises(ValueError):
        run(tmp_path, tmp_path / "data")


def test_intraday_bars_follow_the_daily_sessions(tmp_path):
    from algoverse.backtest.candle_files import import_intraday, intraday_dir

    root, data = tmp_path / "files", tmp_path / "data"
    write_folder(root, stray=True)
    result = run(root, data)
    intraday = import_intraday(root, data, "NIFTY")
    assert intraday.rows > 0 and intraday.sessions == len(result.written)
    bars = pd.read_parquet(intraday_dir(data))
    # Two one-minute candles (09:15 and 09:16) make one 5-minute bar starting at 09:15.
    first = bars.sort_values(["date", "t"]).iloc[0]
    start = pd.Timestamp(int(first["t"]), unit="ms", tz="UTC").tz_convert(IST)
    assert (start.hour, start.minute) == (9, 15)
    assert (first["open"], first["close"], first["volume"]) == (100.0, 102.0, 150.0)
    # Only sessions and contracts in the daily history, so stray bars stay out.
    days = {d.date() if hasattr(d, "date") else d for d in pd.to_datetime(bars["date"])}
    assert days <= set(History(data).sessions) and set(bars["symbol"]) == {"NIFTY"}


def test_intraday_needs_daily_sessions_first(tmp_path):
    from algoverse.backtest.candle_files import import_intraday

    root = tmp_path / "files"
    write_folder(root)
    with pytest.raises(ValueError):
        import_intraday(root, tmp_path / "data", "NIFTY")

import json
from datetime import date

import pandas as pd
import pytest
from algoverse.backtest.bhavcopy import COLUMNS, store_day
from algoverse.backtest.candle_files import intraday_dir
from algoverse.backtest.config import load_backtest_config
from algoverse.backtest.dataview import INCOMPLETE_FILE, History
from algoverse.backtest.upstox_expired_gaps import fill_expired, plan
from conftest import CONFIG_DIR, IST

TODAY = date(2026, 11, 13)
DAYS = [date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28), date(2026, 10, 29)]
NIFTY_EXPIRIES = [date(2026, 10, 27), date(2026, 11, 3), date(2026, 11, 10), date(2026, 11, 17)]
BANK_EXPIRIES = [date(2026, 10, 28), date(2026, 11, 24), date(2026, 12, 29)]
KEYS = {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank"}


def rows(day, symbol, expiries):
    base = {
        "date": day,
        "symbol": symbol,
        "instrument": "OPT",
        "strike": 22700.0,
        "option_type": "CE",
        "open": 1.0,
        "high": 1.0,
        "low": 1.0,
        "close": 7.0,
        "settle": 7.0,
        "volume": 1.0,
        "oi": 1.0,
        "oi_change": 0.0,
        "lot_size": 65,
        "underlying_price": 22750.0,
    }
    frame = pd.DataFrame([base | {"expiry": e} for e in expiries])
    frame["option_type"] = frame["option_type"].astype(object)
    return frame[COLUMNS]


def index_candles(base):
    return pd.DataFrame(
        {
            "ts": [pd.Timestamp(d).tz_localize(IST) for d in DAYS],
            "open": base,
            "high": base + 50,
            "low": base - 50,
            "close": base,
            "volume": 0.0,
            "oi": 0.0,
        }
    )


class Market:
    def __init__(self):
        self.requests = []

    def expired_expiries(self, key):
        listed = NIFTY_EXPIRIES if key == KEYS["NIFTY"] else BANK_EXPIRIES
        return [e for e in listed if e < TODAY]

    def expired_contracts(self, key, kind, expiry):
        self.requests.append(("contracts", key, expiry))
        return [
            {
                "instrument_key": f"{key}|{expiry}|{strike}{kind_}",
                "strike_price": strike,
                "instrument_type": kind_,
                "lot_size": 30 if "Bank" in key else 65,
            }
            for strike in (22700, 99000)
            for kind_ in ("CE", "PE")
        ]

    def _candles(self, start, end, minutes):
        out = []
        for day in DAYS:
            if not start <= day <= end:
                continue
            first = pd.Timestamp(day).tz_localize(IST) + pd.Timedelta(hours=9, minutes=15)
            count = 77 if minutes == 5 else 1  # Upstox adds bars after 15:30.
            for i in range(count):
                out.append(
                    {
                        "ts": first + pd.Timedelta(minutes=minutes * i),
                        "open": 10.0 + i,
                        "high": 11.0 + i,
                        "low": 9.0 + i,
                        "close": 10.5 + i,
                        "volume": 3.0,
                        "oi": 100.0 + i,
                    }
                )
        return pd.DataFrame(out)

    def expired_minute_candles(self, key, minutes, start, end):
        self.requests.append(("minutes", key, start, end))
        return self._candles(start, end, minutes)

    def expired_daily_candles(self, key, start, end):
        self.requests.append(("daily", key, start, end))
        return self._candles(start, end, 1440)

    def daily_candles(self, key, start, end):
        self.requests.append(("live daily", key))
        return self._candles(start, end, 1440)

    def minute_range(self, key, start, end):
        self.requests.append(("live minutes", key))
        return self._candles(start, end, 1)


def contracts():
    out = []
    for symbol, expiries, lot in (
        ("NIFTY", NIFTY_EXPIRIES, 65),
        ("BANKNIFTY", BANK_EXPIRIES, 30),
    ):
        for expiry in [e for e in expiries if e >= TODAY]:
            out.append(
                {
                    "underlying_symbol": symbol,
                    "underlying_key": KEYS[symbol],
                    "instrument_key": f"live|{symbol}|{expiry}",
                    "expiry": expiry,
                    "strike": 22700.0,
                    "option_type": "CE",
                    "lot_size": lot,
                }
            )
    return pd.DataFrame(out)


@pytest.fixture
def setup(tmp_path):
    # 26 Oct came from another source with Nifty only, and has 5-minute bars.
    store_day(tmp_path, rows(DAYS[0], "NIFTY", NIFTY_EXPIRIES[:3]))
    folder = intraday_dir(tmp_path) / f"date={DAYS[0].isoformat()}"
    folder.mkdir(parents=True)
    pd.DataFrame({"symbol": ["NIFTY"], "t": [0]}).to_parquet(folder / "nifty_0.parquet")
    # 28 Oct was filled from live contracts: the 3 Nov and 10 Nov weeklies are missing.
    store_day(tmp_path, rows(DAYS[2], "NIFTY", [NIFTY_EXPIRIES[3]]))
    marks = {DAYS[2].isoformat(): {"NIFTY": ["2026-11-03", "2026-11-10"]}}
    path = tmp_path / INCOMPLETE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marks))
    # 27 Oct and 29 Oct are missing altogether.
    config = load_backtest_config(CONFIG_DIR).model_copy(
        update={
            "symbols": ["NIFTY", "BANKNIFTY"],
            "expiries_per_underlying": 3,
            "strike_range": 0.15,
        }
    )
    candles = {"NIFTY": index_candles(22750.0), "BANKNIFTY": index_candles(22800.0)}
    return tmp_path, config, candles


def test_plan_lists_only_what_sessions_lack():
    held = {date(2026, 10, 26): {("NIFTY", date(2026, 10, 27))}}
    need = plan([date(2026, 10, 26)], held, {"NIFTY": NIFTY_EXPIRIES}, 2)
    assert need == {("NIFTY", date(2026, 11, 3)): {date(2026, 10, 26)}}


def test_fills_holes_with_expired_contracts(setup):
    data_dir, config, candles = setup
    market = Market()
    result = fill_expired(market, data_dir, config, contracts(), candles, TODAY)
    history = History(data_dir)
    assert history.sessions == DAYS[:3] + [DAYS[3]]
    assert result.written == [DAYS[1], DAYS[3]] and result.extended == [DAYS[0], DAYS[2]]

    # The imported Nifty rows are untouched; Bank Nifty and the index rows are added.
    first = history.day(DAYS[0])
    old = first[(first["symbol"] == "NIFTY") & (first["instrument"] == "OPT")]
    assert len(old) == 3 and set(old["close"]) == {7.0}
    assert set(first["symbol"]) == {"NIFTY", "BANKNIFTY"}
    assert set(first.loc[first["symbol"] == "BANKNIFTY", "instrument"]) == {"OPT", "IDX"}
    # Strikes far from spot are left out.
    assert first["strike"].max() < 99000

    # 28 Oct now holds its nearest three Nifty expiries, so its mark is cleared.
    day = history.day(DAYS[2])
    nifty = set(day.loc[(day["symbol"] == "NIFTY") & (day["instrument"] == "OPT"), "expiry"])
    assert {date(2026, 11, 3), date(2026, 11, 10)} <= nifty
    assert "2026-10-28" in result.completed and history.entry_block(DAYS[2], "NIFTY") is None
    # Bank Nifty was added to days before its first served expiry (28 Oct), when an earlier
    # one may have been nearest, so those days stay closed to new Bank Nifty trades.
    early = {"BANKNIFTY": ["any before 2026-10-28"]}
    assert result.incomplete == {"2026-10-26": early, "2026-10-27": early}

    # Nifty has 5-minute bars elsewhere, so it gets them here; Bank Nifty does not.
    bars = history.intraday(DAYS[1])
    assert set(bars["symbol"]) == {"NIFTY"} and len(bars) == 3 * 2 * 75
    late = pd.to_datetime(bars["t"], unit="ms", utc=True).dt.tz_convert(IST).max()
    assert (late.hour, late.minute) == (15, 25)
    daily = history.day(DAYS[1])
    row = daily[(daily["symbol"] == "NIFTY") & (daily["expiry"] == date(2026, 11, 3))].iloc[0]
    assert row["open"] == 10.0 and row["close"] == 10.5 + 74

    # Nothing is left to fetch on a second run.
    again = fill_expired(Market(), data_dir, config, contracts(), candles, TODAY)
    assert sum(again.needed.values()) == 0 and not again.written


def test_plan_only_fetches_nothing(setup):
    data_dir, config, candles = setup
    market = Market()
    result = fill_expired(market, data_dir, config, contracts(), candles, TODAY, dry_run=True)
    assert result.needed["NIFTY"] >= 2 and result.needed["BANKNIFTY"] >= 1
    assert market.requests == [] and History(data_dir).sessions == [DAYS[0], DAYS[2]]


def test_rows_before_the_first_served_expiry_are_marked(setup, monkeypatch):
    import test_expired_fill as module

    data_dir, config, _ = setup
    early = date(2026, 10, 20)
    monkeypatch.setattr(module, "DAYS", [early, *DAYS])
    store_day(data_dir, rows(early, "NIFTY", NIFTY_EXPIRIES[:3]))
    candles = {"NIFTY": index_candles(22750.0), "BANKNIFTY": index_candles(22800.0)}
    result = fill_expired(Market(), data_dir, config, contracts(), candles, TODAY)
    # Bank Nifty's first served expiry is 28 Oct; on 20 Oct an earlier one may be missing.
    assert result.incomplete["2026-10-20"]["BANKNIFTY"][0] == "any before 2026-10-28"
    history = History(data_dir)
    assert history.entry_block(early, "BANKNIFTY") is not None
    assert history.entry_block(early, "NIFTY") is None
    # A later run keeps the mark although it no longer added those rows.
    fill_expired(Market(), data_dir, config, contracts(), candles, TODAY)
    assert History(data_dir).entry_block(early, "BANKNIFTY") is not None

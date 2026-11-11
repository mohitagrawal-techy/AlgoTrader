import shutil
from datetime import date, datetime, time

import numpy as np
import pandas as pd
import pytest
from algoverse.backtest.dataview import History
from algoverse.backtest.engine import ReplaySpec
from algoverse.backtest.intraday import decision_times, replay_intraday
from algoverse.data.candles import MINUTE, CandleStore
from algoverse.lab.spec import CustomSet, apply_specs
from conftest import CONFIG_DIR, IST
from test_backtest import market_rows, save, weekdays
from test_lab import SPREAD, spec

NIFTY = "NSE_INDEX|Nifty 50"


def add_intraday(data, rng):
    """Minute index candles and 5-minute option bars for every saved session."""
    history = History(data)
    store = CandleStore(data)
    previous = None
    for session in history.sessions:
        day = history.day(session)
        close = float(day.loc[day["instrument"] == "FUT", "close"].iloc[0])
        start = previous if previous is not None else close
        path = np.linspace(start, close, 375) + rng.normal(0, 4, 375)
        path[-1] = close
        ts = pd.date_range(f"{session} 09:15", periods=375, freq="min", tz=IST)
        store.write(
            NIFTY,
            pd.DataFrame(
                {"ts": ts, "open": path, "high": path + 3, "low": path - 3, "close": path}
            ),
            MINUTE,
        )
        options = day[day["instrument"] == "OPT"]
        starts = pd.date_range(f"{session} 09:15", periods=75, freq="5min", tz=IST)
        t = starts.as_unit("ms").asi8
        frames = []
        for row in options.itertuples():
            drift = 1 + 0.002 * np.sin(np.arange(75) / 9)
            prices = row.close * drift
            frames.append(
                pd.DataFrame(
                    {
                        "symbol": row.symbol,
                        "t": t,
                        "expiry": row.expiry,
                        "strike": row.strike,
                        "option_type": row.option_type,
                        "open": prices,
                        "high": prices,
                        "low": prices,
                        "close": prices,
                        "volume": row.volume / 75,
                        "oi": row.oi,
                    }
                )
            )
        folder = data / "history" / "fo" / "intraday" / f"date={session}"
        folder.mkdir(parents=True)
        pd.concat(frames, ignore_index=True).to_parquet(folder / "nifty_0.parquet")
        previous = close


@pytest.fixture(scope="module")
def intraday_dirs(tmp_path_factory):
    root = tmp_path_factory.mktemp("intraday")
    config_dir = root / "config"
    shutil.copytree(CONFIG_DIR, config_dir)
    text = (config_dir / "backtest.yaml").read_text()
    text = (
        text.replace("symbols: [NIFTY, BANKNIFTY, FINNIFTY]", "symbols: [NIFTY]")
        .replace("warmup_sessions: 60", "warmup_sessions: 30")
        .replace("workers: 6", "workers: 1")
        .replace("min_sessions: 60", "min_sessions: 20")
        .replace("min_test_trades: 15", "min_test_trades: 1")
    )
    (config_dir / "backtest.yaml").write_text(text)
    events = config_dir / "events.yaml"
    events.write_text(
        events.read_text().replace("reviewed_through: null", "reviewed_through: 2030-01-01")
    )
    data = root / "data"
    rng = np.random.default_rng(11)
    sessions = weekdays("2026-01-05", 50)
    save(data, market_rows(sessions, 23000 + np.cumsum(rng.normal(8, 100, len(sessions)))))
    add_intraday(data, rng)
    return data, config_dir


def run(intraday_dirs, strategy, every=15, days=8):
    from algoverse.backtest.walkforward import load_services

    data, config_dir = intraday_dirs
    services = apply_specs(load_services(config_dir), [strategy])
    sessions = History(data).sessions
    minutes = {"NIFTY": CandleStore(data).read(NIFTY, MINUTE)}
    return replay_intraday(
        History(data),
        services,
        ReplaySpec(sessions[-days], sessions[-1], 0.0),
        minutes,
        every,
        custom=CustomSet([strategy]),
    )


@pytest.mark.parametrize(
    ("every", "count", "first", "last"),
    [(5, 74, "09:20", "15:25"), (15, 24, "09:30", "15:15"), (60, 6, "10:15", "15:15")],
)
def test_decision_times(every, count, first, last):
    times = decision_times(date(2026, 3, 2), every)
    assert len(times) == count
    assert (f"{times[0]:%H:%M}", f"{times[-1]:%H:%M}") == (first, last)


def test_intraday_trades_fill_at_decision_times(intraday_dirs):
    strategy = spec(decide_every="15m")
    result = run(intraday_dirs, strategy)
    assert len(result.details) > 0
    for trade in result.details:
        opened = datetime.fromisoformat(trade["opened_at"]).astimezone(IST)
        assert opened.minute % 15 == 0 and time(9, 30) <= opened.time() <= time(15, 15)


def test_square_off_closes_every_trade_the_same_day(intraday_dirs):
    strategy = spec(decide_every="15m", exits={**SPREAD["exits"], "square_off": "14:30"})
    result = run(intraday_dirs, strategy)
    assert len(result.details) > 0
    for trade in result.details:
        opened = datetime.fromisoformat(trade["opened_at"]).astimezone(IST)
        closed = datetime.fromisoformat(trade["closed_at"]).astimezone(IST)
        assert opened.date() == closed.date() and opened.time() < time(14, 30)
        assert closed.time() <= time(14, 45) or trade["exit_reason"] == "end_of_test"
    assert {t["exit_reason"] for t in result.details} & {"square_off", "target", "stop"}


def test_entry_window_limits_entries(intraday_dirs):
    strategy = spec(
        decide_every="15m",
        entry={**SPREAD["entry"], "time_from": "11:00", "time_to": "12:00"},
    )
    result = run(intraday_dirs, strategy)
    for trade in result.details:
        opened = datetime.fromisoformat(trade["opened_at"]).astimezone(IST)
        # Decided between 11:00 and 12:00, so filled at the next decision time.
        assert time(11, 15) <= opened.time() <= time(12, 15)


def test_minute_rules_only_see_finished_candles(intraday_dirs):
    never = {
        "type": "compare",
        "timeframe": "5m",
        "left": {"name": "close"},
        "op": "below",
        "right": {"kind": "value", "value": 0},
    }
    always = {
        "type": "compare",
        "timeframe": "15m",
        "left": {"name": "close"},
        "op": "above",
        "right": {"kind": "value", "value": 0},
    }
    blocked = run(
        intraday_dirs, spec(decide_every="15m", entry={**SPREAD["entry"], "rules": [never]})
    )
    allowed = run(
        intraday_dirs, spec(decide_every="15m", entry={**SPREAD["entry"], "rules": [always]})
    )
    assert blocked.details == [] and len(allowed.details) > 0


def test_daily_strategies_cannot_use_minute_rules_or_times():
    rule = {"type": "pattern", "timeframe": "1h", "pattern": "doji"}
    with pytest.raises(ValueError):
        spec(entry={**SPREAD["entry"], "rules": [rule]})
    with pytest.raises(ValueError):
        spec(exits={**SPREAD["exits"], "square_off": "15:00"})
    with pytest.raises(ValueError):
        spec(decide_every="15m", exits={**SPREAD["exits"], "square_off": "16:00"})


def test_lab_tests_intraday_strategies(intraday_dirs, tmp_path):
    from algoverse.lab.service import LabService

    data, config_dir = intraday_dirs
    copy = tmp_path / "data"
    shutil.copytree(data, copy)
    lab = LabService(copy, config_dir, True, None)
    saved = lab.save(
        {**SPREAD, "decide_every": "15m", "exits": {**SPREAD["exits"], "square_off": "15:00"}},
        datetime(2026, 4, 1, tzinfo=IST),
    )
    lab.run_backtest([saved.id], datetime(2026, 4, 1, tzinfo=IST))
    result = lab.result(saved)
    assert result["train"]["trades"] + result["test"]["trades"] > 0
    trades = result["detail"]["train"]["trades"] + result["detail"]["test"]["trades"]
    assert all(t["opened"] == t["closed"] or t["exit_reason"] == "end_of_test" for t in trades)

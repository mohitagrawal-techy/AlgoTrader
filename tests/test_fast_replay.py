"""Speed-ups in the intraday backtest must not change a single trade."""

import pandas as pd
import pytest
from algoverse.backtest.dataview import History
from algoverse.backtest.engine import ReplaySpec
from algoverse.backtest.intraday import replay_intraday
from algoverse.data.candles import MINUTE, CandleStore
from algoverse.lab.spec import CustomSet, apply_specs
from test_intraday_backtest import NIFTY, intraday_dirs  # noqa: F401
from test_lab import SPREAD, spec

BREAKOUT = {
    "type": "compare",
    "timeframe": "5m",
    "left": {"name": "close"},
    "op": "above",
    "right": {"name": "opening_range", "params": {"minutes": 15}, "field": "high"},
}
RISING = {
    "type": "compare",
    "timeframe": "15m",
    "left": {"name": "close"},
    "op": "above",
    "right": {"name": "ema", "params": {"period": 5}},
}
EXITS = {**SPREAD["exits"], "square_off": "15:00"}
STRATEGIES = {
    "breakout": spec(
        decide_every="5m",
        entry={
            **SPREAD["entry"],
            "rules": [BREAKOUT],
            "time_from": "09:30",
            "time_to": "14:30",
            "max_trades_per_day": 1,
        },
        exits=EXITS,
    ),
    "window": spec(
        decide_every="15m", entry={**SPREAD["entry"], "time_from": "11:00", "time_to": "13:00"}
    ),
    "trend_and_limit": spec(
        decide_every="15m",
        entry={**SPREAD["entry"], "rules": [RISING], "max_trades_per_day": 2},
        exits=EXITS,
    ),
}


def replay(intraday_dirs, strategy, fast):  # noqa: F811
    from algoverse.backtest.walkforward import load_services

    data, config_dir = intraday_dirs
    services = apply_specs(load_services(config_dir), [strategy])
    sessions = History(data).sessions
    skipped = []
    every = {"5m": 5, "15m": 15}[strategy.decide_every]
    result = replay_intraday(
        History(data),
        services,
        ReplaySpec(sessions[-8], sessions[-1], 0.0),
        {"NIFTY": CandleStore(data).read(NIFTY, MINUTE)},
        every,
        custom=CustomSet([strategy]),
        on_skip=skipped.append,
        fast=fast,
    )
    return result, skipped


@pytest.mark.parametrize("name", sorted(STRATEGIES))
def test_skipping_idle_decisions_changes_nothing(intraday_dirs, name):  # noqa: F811
    strategy = STRATEGIES[name]
    slow, never = replay(intraday_dirs, strategy, fast=False)
    fast, skipped = replay(intraday_dirs, strategy, fast=True)
    assert never == [] and skipped, "the fast run should skip some decisions"
    assert fast.details == slow.details
    pd.testing.assert_series_equal(fast.daily_pnl, slow.daily_pnl)
    pd.testing.assert_frame_equal(fast.trades, slow.trades)
    assert all(reason.startswith("entry rule") for batch in skipped for reason in batch)


@pytest.mark.parametrize("name", ["breakout", "trend_and_limit"])
def test_chunked_windows_match_one_long_run(intraday_dirs, name, monkeypatch):  # noqa: F811
    from algoverse.lab import evaluate

    data, config_dir = intraday_dirs
    strategy = STRATEGIES[name]
    sessions = History(data).sessions
    task = (
        str(data),
        str(config_dir),
        strategy.model_dump_json(),
        sessions[-12],
        sessions[-1],
        1.0,
    )
    whole = evaluate.run_tasks([task], 1)[0]
    monkeypatch.setattr(evaluate, "MIN_CHUNK_SESSIONS", 3)
    plan = evaluate._plan([(*task, None)], 2)
    assert len(plan) >= 3, "the window should be split"
    assert all(sub[6] == task[3] for _, sub in plan)
    calls = []
    chunked = evaluate.run_tasks([task], 2, sessions, lambda d, t: calls.append((d, t)))[0]
    # Daily P&L is the change in a running total, so chunks that start their totals afresh
    # differ only in the last floating-point digit.
    for key in ("daily", "stats"):
        # Figures with no value, such as a recovery factor with no drawdown, must match exactly.
        empty = {k for k, v in whole[key].items() if v is None}
        assert {k for k, v in chunked[key].items() if v is None} == empty
        numbers = {k: v for k, v in whole[key].items() if k not in empty}
        assert {k: chunked[key][k] for k in numbers} == pytest.approx(numbers, rel=1e-9, abs=1e-6)
    rest = {k: v for k, v in whole.items() if k not in ("daily", "stats", "equity")}
    assert {k: chunked[k] for k in rest} == rest
    assert calls[-1] == (12, 12)


def test_daily_strategies_are_never_split(intraday_dirs, monkeypatch):  # noqa: F811
    from algoverse.lab import evaluate

    data, config_dir = intraday_dirs
    daily = spec()
    sessions = History(data).sessions
    task = (str(data), str(config_dir), daily.model_dump_json(), sessions[-12], sessions[-1], 1.0)
    monkeypatch.setattr(evaluate, "MIN_CHUNK_SESSIONS", 3)
    assert len(evaluate._plan([(*task, None)], 4)) == 1


@pytest.mark.parametrize(("count", "workers"), [(12, 2), (13, 2), (15, 3), (31, 4), (47, 6)])
def test_chunks_cover_every_session_once(intraday_dirs, monkeypatch, count, workers):  # noqa: F811
    from algoverse.lab import evaluate

    data, config_dir = intraday_dirs
    history = History(data)
    days = [d for d in history.sessions if history.has_intraday(d)][-count:]
    task = (
        str(data),
        str(config_dir),
        STRATEGIES["breakout"].model_dump_json(),
        days[0],
        days[-1],
        1.0,
        None,
    )
    monkeypatch.setattr(evaluate, "MIN_CHUNK_SESSIONS", 3)
    plan = [sub for _, sub in evaluate._plan([task], workers)]
    covered = [d for sub in plan for d in days if sub[3] <= d <= sub[4]]
    assert covered == days
    assert all(len([d for d in days if sub[3] <= d <= sub[4]]) >= 3 for sub in plan)


def test_contract_key_of_an_empty_session_is_empty_text():
    from algoverse.backtest.fills import contract_key

    empty = pd.DataFrame(
        {
            "expiry": pd.Series([], dtype=object),
            "strike": [],
            "option_type": pd.Series([], dtype=str),
        }
    )
    keys = contract_key("", empty)
    assert keys.empty and keys.map({"x": 1.0}).fillna(0.0).empty

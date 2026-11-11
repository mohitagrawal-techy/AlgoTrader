import json
import zipfile
from dataclasses import replace
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from algoverse.analytics.chain import years_to_expiry
from algoverse.analytics.pricing import black76_price
from algoverse.backtest.bhavcopy import (
    COLUMNS,
    BhavcopyError,
    import_files,
    parse_bhavcopy,
    store_day,
)
from algoverse.backtest.config import load_backtest_config
from algoverse.backtest.dataview import DataView, History
from algoverse.backtest.engine import ReplaySpec, Services, replay
from algoverse.backtest.fills import fit_spread_model, modelled_chain
from algoverse.backtest.registry import Registry
from algoverse.backtest.report import (
    daily_stats,
    max_drawdown,
    monte_carlo,
    summarize,
    trade_stats,
)
from algoverse.backtest.walkforward import (
    build_folds,
    choose_best,
    parameter_grid,
    run_holdout,
    run_walk_forward,
)
from algoverse.evaluation.config import EventCalendar
from conftest import CONFIG_DIR, IST, make_bars

CALENDAR = EventCalendar(reviewed_through=date(2030, 1, 1))
UDIFF_HEADER = (
    "TradDt,FinInstrmTp,TckrSymb,XpryDt,StrkPric,OptnTp,OpnPric,HghPric,LwPric,ClsPric,"
    "SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,NewBrdLotQty,UndrlygPric\n"
)
UDIFF_ROW = "2026-03-02,IDO,NIFTY,2026-03-10,24000,CE,100,110,90,105,104,5000,100,9000,65,24010\n"
LEGACY = (
    "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,"
    "OPEN_INT,CHG_IN_OI,TIMESTAMP\n"
    "OPTIDX,NIFTY,30-Apr-2020,9000,CE,100,110,90,105,104,200,5000,100,02-Apr-2020\n"
    "OPTSTK,TCS,30-Apr-2020,2000,CE,10,11,9,10,10,20,50,1,02-Apr-2020\n"
)
LOTS = {"NIFTY": [(date(2019, 1, 1), 75)]}


def test_udiff_file_is_normalised():
    frame = parse_bhavcopy((UDIFF_HEADER + UDIFF_ROW).encode(), ["NIFTY"], {})
    row = frame.iloc[0]
    assert list(frame.columns) == COLUMNS
    assert row["date"] == date(2026, 3, 2) and row["expiry"] == date(2026, 3, 10)
    assert (row["instrument"], row["strike"], row["option_type"]) == ("OPT", 24000.0, "CE")
    assert (row["close"], row["lot_size"], row["underlying_price"]) == (105.0, 65, 24010.0)


def test_legacy_file_uses_dated_lot_table_and_drops_other_symbols():
    frame = parse_bhavcopy(LEGACY.encode(), ["NIFTY"], LOTS)
    assert len(frame) == 1
    assert frame.iloc[0]["lot_size"] == 75
    assert frame.iloc[0]["volume"] == 200 * 75


def test_legacy_file_without_a_lot_size_is_refused():
    with pytest.raises(BhavcopyError, match="lot_sizes"):
        parse_bhavcopy(LEGACY.encode(), ["NIFTY"], {})


def test_unknown_columns_are_refused_with_the_missing_names():
    with pytest.raises(BhavcopyError, match="OpnPric"):
        parse_bhavcopy(
            UDIFF_HEADER.replace("OpnPric", "Open").encode() + UDIFF_ROW.encode(), ["NIFTY"], {}
        )


def test_import_handles_zip_folders_and_bad_files(tmp_path):
    source = tmp_path / "downloads"
    source.mkdir()
    with zipfile.ZipFile(source / "good.zip", "w") as archive:
        archive.writestr("day.csv", UDIFF_HEADER + UDIFF_ROW)
    (source / "bad.csv").write_text("a,b\n1,2\n")
    result = import_files([source], tmp_path / "data", ["NIFTY"], {})
    assert result.imported == [date(2026, 3, 2)]
    assert list(result.failed) == ["bad.csv"]
    assert History(tmp_path / "data").sessions == [date(2026, 3, 2)]


def test_dataview_never_returns_the_current_session_or_later():
    bars = make_bars(end=date(2026, 3, 10), count=20)
    view = DataView({"X": bars})
    view.as_of = date(2026, 3, 5)
    assert view.read_candles("X")["ts"].iloc[-1].date() < date(2026, 3, 5)
    assert view.read_candles("MISSING").empty


def test_dataview_filters_written_series():
    view = DataView({})
    view.upsert_candles("iv", pd.DataFrame({"ts": [pd.Timestamp("2026-03-05")], "close": [1.0]}))
    view.as_of = date(2026, 3, 5)
    assert view.read_candles("iv").empty
    view.as_of = date(2026, 3, 6)
    assert len(view.read_candles("iv")) == 1


def spread_settings():
    return load_backtest_config(CONFIG_DIR).spread.model_copy(
        update={"base_pct": 1.0, "moneyness_pct_per_pct": 0.0, "illiquidity_pct": 0.0}
    )


def test_modelled_quotes_and_untraded_contracts():
    options = pd.DataFrame(
        {
            "expiry": [date(2026, 3, 10)] * 2,
            "strike": [24000.0, 24100.0],
            "option_type": ["CE", "CE"],
            "open": [100.0, 0.0],
            "volume": [6500.0, 0.0],
            "lot_size": [65, 65],
            "oi": [10.0, 10.0],
            "oi_change": [1.0, 1.0],
            "prev_close": [90.0, 90.0],
        }
    )
    now = datetime(2026, 3, 2, 9, 15, tzinfo=IST)
    chain = modelled_chain("NIFTY", options, "open", 24000.0, now, spread_settings())
    assert (chain.loc[0, "bid"], chain.loc[0, "ask"]) == (99.5, 100.5)
    assert chain.loc[0, "ask_qty"] == 130
    assert np.isnan(chain.loc[1, "bid"]) and np.isnan(chain.loc[1, "ask"])
    wider = modelled_chain("NIFTY", options, "open", 24000.0, now, spread_settings(), 2.0)
    assert (wider.loc[0, "bid"], wider.loc[0, "ask"]) == (99.0, 101.0)


def test_spread_model_recovers_known_parameters():
    rng = np.random.default_rng(1)
    count = 500
    strike = 24000 * (1 + rng.uniform(-0.05, 0.05, count))
    volume = rng.uniform(100, 500000, count)
    pct = 0.4 + 0.2 * np.abs(strike / 24000 - 1) * 100 + 3.0 / np.sqrt(1 + volume / 65)
    mid = rng.uniform(20, 300, count)
    snapshots = pd.DataFrame(
        {
            "strike": strike,
            "spot": 24000.0,
            "volume": volume,
            "bid": mid * (1 - pct / 200),
            "ask": mid * (1 + pct / 200),
        }
    )
    fitted = fit_spread_model(snapshots, 65)
    assert fitted["base_pct"] == pytest.approx(0.4, abs=1e-6)
    assert fitted["moneyness_pct_per_pct"] == pytest.approx(0.2, abs=1e-6)
    assert fitted["illiquidity_pct"] == pytest.approx(3.0, abs=1e-6)


def test_max_drawdown_counts_from_zero():
    assert max_drawdown(np.array([-5.0, 10.0, 4.0, 8.0])) == 6.0
    assert max_drawdown(np.array([])) == 0.0


def stats_trades(pnl):
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(len(pnl))]
    return pd.DataFrame({"net_pnl": pnl, "closed": days, "costs": 1.0, "holding_days": 2})


def test_trade_stats_hand_calculation():
    stats = trade_stats(stats_trades([100.0, -50.0, -30.0, 200.0]), 0.25)
    assert stats["win_rate"] == 0.5
    assert stats["expectancy"] == 55.0
    assert stats["profit_factor"] == pytest.approx(300 / 80)
    assert stats["longest_losing_streak"] == 2
    assert stats["tail_loss"] == -50.0
    assert stats["costs"] == 4.0


def test_daily_stats_hand_calculation():
    days = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 2, 2), date(2026, 2, 3)]
    daily = pd.Series([1000.0, -500.0, -800.0, 100.0], index=days)
    stats = daily_stats(daily, 100000.0)
    returns = np.array([0.01, -0.005, -0.008, 0.001])
    assert stats["sharpe"] == pytest.approx(returns.mean() / returns.std(ddof=1) * np.sqrt(252))
    assert stats["worst_day"] == -800.0
    assert stats["max_drawdown"] == 1300.0
    assert stats["negative_months_pct"] == 50.0


def test_monte_carlo_keeps_total_and_is_repeatable():
    pnl = np.array([100.0, -300.0, 50.0, -20.0, 400.0, -150.0])
    first = monte_carlo(pnl, 500, 100000.0)
    assert first == monte_carlo(pnl, 500, 100000.0)
    assert first["drawdown_p5"] <= first["drawdown_p50"] <= first["drawdown_p95"]
    assert monte_carlo(np.array([1.0]), 10, 1.0) is None


def test_folds_never_overlap_and_exclude_the_holdout():
    config = load_backtest_config(CONFIG_DIR)
    sessions = [date(2020, 1, 1) + timedelta(days=i) for i in range(700)]
    folds, holdout = build_folds(sessions, config)
    assert folds and len(holdout) == config.walk_forward.holdout_sessions
    for fold in folds:
        assert fold.train_end < fold.test_start
        assert fold.test_end < holdout[0]
    for first, second in zip(folds, folds[1:], strict=False):
        assert first.test_end < second.test_start


def test_folds_are_empty_with_too_little_history():
    config = load_backtest_config(CONFIG_DIR)
    assert build_folds([date(2020, 1, 1)], config)[0] == []


def test_grid_is_the_full_cross_product():
    config = load_backtest_config(CONFIG_DIR)
    assert len(parameter_grid(config)) == len(config.grid.min_score) * len(
        config.grid.disabled_profiles
    )


# ---- Synthetic market -------------------------------------------------------------------------

SYMBOL = "NIFTY"
R = 0.055


def weekdays(start, count):
    days = pd.bdate_range(start=pd.Timestamp(start), periods=count)
    return [d.date() for d in days]


def future_row(session, close, expiry, lot=65, day_range=20.0, open_price=None):
    return {
        "date": session,
        "symbol": SYMBOL,
        "instrument": "FUT",
        "expiry": expiry,
        "strike": 0.0,
        "option_type": None,
        "open": close if open_price is None else open_price,
        "high": close + day_range,
        "low": close - day_range,
        "close": close,
        "settle": close,
        "volume": 100000.0,
        "oi": 100000.0,
        "oi_change": 0.0,
        "lot_size": lot,
        "underlying_price": close,
    }


def option_row(session, expiry, strike, kind, price, volume, lot=65, spot=24000.0):
    return {
        "date": session,
        "symbol": SYMBOL,
        "instrument": "OPT",
        "expiry": expiry,
        "strike": strike,
        "option_type": kind,
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "settle": price,
        "volume": volume,
        "oi": 500000.0,
        "oi_change": 1000.0,
        "lot_size": lot,
        "underlying_price": spot,
    }


def save(data_dir, rows):
    frame = pd.DataFrame(rows)
    for _, group in frame.groupby("date"):
        store_day(data_dir, group[COLUMNS].reset_index(drop=True))


def next_tuesday(session, weeks_ahead=1):
    days = (1 - session.weekday()) % 7 or 7
    return session + timedelta(days=days + 7 * (weeks_ahead - 1))


def market_rows(sessions, closes, sigma=0.14, volume=800000.0):
    rows = []
    for session, close in zip(sessions, closes, strict=True):
        expiries = [next_tuesday(session, weeks) for weeks in (1, 2, 3)]
        rows.append(future_row(session, close, expiries[-1], day_range=close * 0.002))
        for expiry in expiries:
            T = years_to_expiry(
                expiry, datetime(session.year, session.month, session.day, 15, 25, tzinfo=IST)
            )
            forward = close * np.exp(R * T)
            center = round(close / 50) * 50
            strikes = np.arange(center - 1000, center + 1001, 50.0)
            for kind, is_call in (("CE", True), ("PE", False)):
                prices = np.maximum(black76_price(forward, strikes, T, sigma, R, is_call), 1.0)
                rows += [
                    option_row(session, expiry, float(k), kind, float(p), volume, spot=close)
                    for k, p in zip(strikes, prices, strict=True)
                ]
    return rows


def services(risk_config, scoring_config, cost_model, **backtest_changes):
    backtest = load_backtest_config(CONFIG_DIR).model_copy(update=backtest_changes)
    return Services(backtest, scoring_config, risk_config, cost_model, CALENDAR)


@pytest.fixture(scope="module")
def market_dir(tmp_path_factory):
    folder = tmp_path_factory.mktemp("market")
    rng = np.random.default_rng(5)
    sessions = weekdays("2026-01-05", 110)
    closes = 23000 + np.cumsum(rng.normal(12, 110, len(sessions)))
    save(folder, market_rows(sessions, closes))
    return folder


def run(data_dir, svc, start, end, min_score=0.0, **kwargs):
    return replay(History(data_dir), svc, ReplaySpec(start, end, min_score, **kwargs))


def test_replay_produces_trades_after_warmup_only(
    market_dir, risk_config, scoring_config, cost_model
):
    svc = services(risk_config, scoring_config, cost_model, symbols=[SYMBOL])
    sessions = History(market_dir).sessions
    start = sessions[70]
    result = run(market_dir, svc, start, sessions[100])
    assert len(result.trades) > 0
    assert (result.trades["opened"] >= start).all()
    assert all(day >= start for day in result.daily_pnl.index)
    assert result.sessions == 31
    assert set(result.trades.columns) >= {"trend", "iv_regime", "dte_at_entry", "net_pnl"}
    # Each closed trade is also listed with its legs, in the same total.
    assert len(result.details) == len(result.trades)
    assert sum(d["net_pnl"] for d in result.details) == pytest.approx(
        result.trades["net_pnl"].sum()
    )
    first = result.details[0]
    assert first["opened"] <= first["closed"] and first["legs"]
    assert {"side", "option_type", "strike", "entry_price", "exit_price"} <= set(first["legs"][0])


def test_changing_future_bars_leaves_earlier_decisions_unchanged(
    tmp_path, risk_config, scoring_config, cost_model
):
    svc = services(risk_config, scoring_config, cost_model, symbols=[SYMBOL])
    rng = np.random.default_rng(9)
    sessions = weekdays("2026-01-05", 95)
    closes = 23000 + np.cumsum(rng.normal(10, 100, len(sessions)))
    original, altered = tmp_path / "a", tmp_path / "b"
    save(original, market_rows(sessions, closes))
    changed = closes.copy()
    cut = 85
    changed[cut:] = changed[cut:] * 1.25
    save(altered, market_rows(sessions, changed))
    start, boundary = sessions[70], sessions[cut - 1]

    def decisions(folder):
        result = run(folder, svc, start, sessions[-1])
        early = result.trades[result.trades["opened"] <= boundary]
        return early[["symbol", "strategy", "lots", "opened", "score"]].reset_index(drop=True)

    pd.testing.assert_frame_equal(decisions(original), decisions(altered))


def test_double_slippage_reduces_pnl(market_dir, risk_config, scoring_config, cost_model):
    svc = services(risk_config, scoring_config, cost_model, symbols=[SYMBOL])
    sessions = History(market_dir).sessions
    start, end = sessions[70], sessions[100]
    base = run(market_dir, svc, start, end)
    wide = run(market_dir, svc, start, end, spread_multiplier=2.0)
    assert wide.trades["net_pnl"].sum() < base.trades["net_pnl"].sum()


def test_disabled_profiles_are_not_traded(market_dir, risk_config, scoring_config, cost_model):
    svc = services(risk_config, scoring_config, cost_model, symbols=[SYMBOL])
    sessions = History(market_dir).sessions
    result = run(
        market_dir, svc, sessions[70], sessions[90], disabled_profiles=("butterfly", "calendar")
    )
    assert not result.trades.empty
    assert not set(result.trades["profile"]) & {"butterfly", "calendar"}


def test_backtest_and_paper_make_identical_decisions(
    market_dir, risk_config, scoring_config, cost_model, tmp_path
):
    """Paper trading fed the same modelled quotes must queue the same ideas as the replay."""
    from algoverse.backtest import engine as eng
    from algoverse.execution.cycle import CycleDeps, prepare_chains, run_cycle
    from algoverse.execution.ledger import Ledger
    from algoverse.execution.paper import PaperBroker

    svc = services(risk_config, scoring_config, cost_model, symbols=[SYMBOL])
    history = History(market_dir)
    sessions = history.sessions
    start, end = sessions[70], sessions[74]
    backtest = run(market_dir, svc, start, end)

    risk = risk_config.model_copy(deep=True)
    risk.paper.min_score = 0.0
    risk.paper.idea_ttl_minutes = svc.backtest.idea_ttl_minutes
    risk.paper.max_entry_drift_pct = svc.backtest.max_entry_drift_pct
    view = DataView({SYMBOL: history.bars(SYMBOL)})
    broker = PaperBroker(Ledger(tmp_path / "paper.sqlite"), risk, cost_model)
    deps = CycleDeps(view, broker, scoring_config, risk, cost_model, CALENDAR, publish=False)
    previous, seen = None, []
    first = sessions.index(start) - svc.backtest.warmup_sessions
    for session in sessions[first : sessions.index(end) + 1]:
        day = history.day(session)
        view.as_of = session
        risk.paper.auto_trade = session >= start
        options = eng._prepare_options(day, previous, SYMBOL, svc.backtest)
        previous = day
        bar = history.bars(SYMBOL)
        row = bar[bar["ts"].dt.date == session].iloc[0]
        spot = eng._spot(day, SYMBOL, session, row)
        low, high = spot * 0.85, spot * 1.15
        options = options[options["strike"].between(low, high)]
        contracts = pd.DataFrame(
            {
                "instrument_key": eng.contract_key(SYMBOL, options).to_numpy(),
                "lot_size": options["lot_size"].to_numpy(),
            }
        )
        open_at, close_at = eng._at(session, eng.OPEN_TIME), eng._at(session, eng.CLOSE_TIME)
        opening = modelled_chain(
            SYMBOL, options, "open", float(row["open"]), open_at, svc.backtest.spread
        )
        broker.step(open_at, prepare_chains({SYMBOL: opening}, contracts, open_at, R))
        closing = modelled_chain(SYMBOL, options, "close", spot, close_at, svc.backtest.spread)
        result = run_cycle(deps, {SYMBOL: closing}, contracts, close_at)
        seen += [
            (p.strategy, p.lots, datetime.fromisoformat(p.created_at).date())
            for p in broker.ledger.positions(("pending",))
            if p.id in result.submitted
        ]
    from_backtest = {(row.strategy, row.lots, row.opened) for row in backtest.trades.itertuples()}
    filled = {
        (strategy, lots, sessions[sessions.index(day) + 1])
        for strategy, lots, day in seen
        if sessions[sessions.index(day) + 1] <= end
    }
    assert from_backtest and from_backtest <= filled


def known_answer_rows():
    """Two long calls with a hand-calculated outcome, spanning a lot and tax change."""
    warmup = weekdays("2025-11-10", 70)
    rows = [
        future_row(d, 24000.0 + i * 4, date(2026, 3, 31), day_range=8.0)
        for i, d in enumerate(warmup)
    ]
    return rows, warmup


def trade_day_rows(session, expiry, call, volume, lot, spot):
    """One tradable call. The put provides parity quotes but its volume fails the filter."""
    return [
        future_row(session, spot, date(2026, 6, 30), lot=lot, day_range=8.0),
        option_row(session, expiry, 24000.0, "CE", call, volume, lot, spot),
        option_row(
            session, expiry, 24000.0, "PE", max(call - (spot - 24000.0), 1.0), 50.0, lot, spot
        ),
    ]


def test_known_answer_settlement_costs_tax_change_and_lot_change(
    tmp_path, risk_config, scoring_config, cost_model
):
    risk = risk_config.model_copy(deep=True)
    risk.exits["directional_buy"].exit_dte = 0
    risk.max_loss_per_trade_pct = 2.0
    svc = services(
        risk,
        scoring_config,
        cost_model,
        symbols=[SYMBOL],
        warmup_sessions=70,
        spread=spread_settings(),
    )
    rows, warmup = known_answer_rows()
    # First trade: lot 75, March tax rates, held to expiry and settled.
    rows += trade_day_rows(date(2026, 3, 2), date(2026, 3, 5), 100.0, 90000.0, 75, 24050.0)
    rows += trade_day_rows(date(2026, 3, 3), date(2026, 3, 5), 100.0, 90000.0, 75, 24050.0)
    rows += trade_day_rows(date(2026, 3, 4), date(2026, 3, 5), 100.0, 50.0, 75, 24050.0)
    rows += trade_day_rows(date(2026, 3, 5), date(2026, 3, 5), 120.0, 50.0, 75, 24120.0)
    rows += [future_row(date(2026, 3, 6), 24120.0, date(2026, 6, 30), lot=75, day_range=8.0)]
    # A session with quotes, needed so the settlement step runs.
    rows += trade_day_rows(date(2026, 3, 9), date(2026, 4, 28), 100.0, 50.0, 75, 24120.0)
    # Second trade: lot 65, April tax rates, also settled at expiry.
    rows += trade_day_rows(date(2026, 4, 20), date(2026, 4, 23), 100.0, 90000.0, 65, 24050.0)
    rows += trade_day_rows(date(2026, 4, 21), date(2026, 4, 23), 100.0, 90000.0, 65, 24050.0)
    rows += trade_day_rows(date(2026, 4, 22), date(2026, 4, 23), 110.0, 50.0, 65, 24050.0)
    rows += trade_day_rows(date(2026, 4, 23), date(2026, 4, 23), 120.0, 50.0, 65, 24120.0)
    rows += trade_day_rows(date(2026, 4, 24), date(2026, 4, 28), 100.0, 50.0, 65, 24120.0)
    save(tmp_path, rows)

    result = run(tmp_path, svc, date(2026, 3, 2), date(2026, 4, 24))
    trades = result.trades.sort_values("opened").reset_index(drop=True)
    assert len(trades) == 2, result.unfilled

    # Entry: 100 + 0.5 half-spread + 0.05 slippage = 100.55. Settles at 24120 - 24000 = 120.
    march = trades.iloc[0]
    assert march["lots"] == 1 and march["exit_reason"] == "time"
    entry = cost_model.order(True, 100.55, 75, date(2026, 3, 3))
    exercise = 120.0 * 75 * 0.125 / 100
    assert march["net_pnl"] == pytest.approx((120.0 - 100.55) * 75 - entry - exercise)

    april = trades.iloc[1]
    assert april["lots"] == 1
    assert april["opened"] == date(2026, 4, 21)
    entry = cost_model.order(True, 100.55, 65, date(2026, 4, 21))
    exercise = 120.0 * 65 * 0.15 / 100
    assert april["net_pnl"] == pytest.approx((120.0 - 100.55) * 65 - entry - exercise)


def test_intraday_stop_is_taken_before_target_when_both_were_reachable(
    tmp_path, risk_config, scoring_config, cost_model
):
    svc = services(
        risk_config,
        scoring_config,
        cost_model,
        symbols=[SYMBOL],
        warmup_sessions=70,
        spread=spread_settings(),
    )
    rows, _ = known_answer_rows()
    expiry = date(2026, 3, 20)
    rows += trade_day_rows(date(2026, 3, 2), expiry, 100.0, 90000.0, 65, 24050.0)
    rows += trade_day_rows(date(2026, 3, 3), expiry, 100.0, 90000.0, 65, 24050.0)
    wide = trade_day_rows(date(2026, 3, 4), expiry, 100.0, 50.0, 65, 24050.0)
    wide[0] = future_row(date(2026, 3, 4), 24050.0, date(2026, 6, 30), lot=65, day_range=1500.0)
    rows += wide
    rows += trade_day_rows(date(2026, 3, 5), expiry, 100.0, 50.0, 65, 24050.0)
    save(tmp_path, rows)
    trades = run(tmp_path, svc, date(2026, 3, 2), date(2026, 3, 5)).trades
    assert trades.iloc[0]["exit_reason"] == "stop"


def test_walk_forward_end_to_end_logs_every_trial(
    tmp_path, risk_config, scoring_config, cost_model, monkeypatch
):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for path in CONFIG_DIR.glob("*.yaml"):
        (config_dir / path.name).write_text(path.read_text())
    custom = (config_dir / "backtest.yaml").read_text()
    custom = (
        custom.replace("symbols: [NIFTY, BANKNIFTY, FINNIFTY]", "symbols: [NIFTY]")
        .replace("warmup_sessions: 60", "warmup_sessions: 55")
        .replace("train_sessions: 252", "train_sessions: 20")
        .replace("test_sessions: 63", "test_sessions: 10")
        .replace("holdout_sessions: 63", "holdout_sessions: 5")
        .replace("min_train_trades: 20", "min_train_trades: 1")
        .replace("workers: 8", "workers: 1")
        .replace("min_score: [50, 55, 60, 65, 70, 75]", "min_score: [0, 50]")
        .replace("    - [directional_buy, long_vol]\n", "")
        .replace("monte_carlo_runs: 2000", "monte_carlo_runs: 50")
    )
    (config_dir / "backtest.yaml").write_text(custom)
    events = (
        (config_dir / "events.yaml")
        .read_text()
        .replace("reviewed_through: null", "reviewed_through: 2030-01-01")
    )
    (config_dir / "events.yaml").write_text(events)

    data = tmp_path / "data"
    rng = np.random.default_rng(13)
    sessions = weekdays("2026-01-05", 100)
    closes = 23000 + np.cumsum(rng.normal(14, 90, len(sessions)))
    save(data, market_rows(sessions, closes))
    now = datetime(2026, 11, 9, 12, 0, tzinfo=IST)

    report = run_walk_forward(data, config_dir, now)
    assert report["folds"]
    assert report["verdict"] in ("insufficient_evidence", "failed", "passed_walk_forward")
    registry = Registry(data)
    assert report["trials_to_date"] == registry.trial_count() >= len(report["folds"]) * 2
    assert report["holdout"]["sessions"] == 5
    for row in report["summary"]["equity"]:
        assert date.fromisoformat(str(row["day"])) < date.fromisoformat(
            str(report["holdout"]["start"])
        )
    saved = registry.read_report(report["id"])
    assert saved["id"] == report["id"]
    json.dumps(saved, allow_nan=False)
    assert registry.list_reports()[0]["kind"] == "walk_forward"

    holdout = run_holdout(data, config_dir, now, 0.0)
    assert holdout["kind"] == "holdout"
    with pytest.raises(PermissionError):
        run_holdout(data, config_dir, now, 0.0)
    assert run_holdout(data, config_dir, now, 0.0, allow_reuse=True)["holdout_uses_before"] == 1


def _result(sharpe_series, trades, score):
    from algoverse.backtest.engine import ReplayResult

    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(len(sharpe_series))]
    frame = pd.DataFrame({"net_pnl": [1.0] * trades})
    return ReplayResult(
        ReplaySpec(days[0], days[-1], score), frame, pd.Series(sharpe_series, index=days), 1
    )


def test_selection_needs_enough_trades_and_a_positive_sharpe():
    good = _result([100.0, 50.0, 120.0], 5, 60.0)
    thin = _result([900.0, 950.0, 990.0], 1, 65.0)
    losing = _result([-100.0, -50.0, -120.0], 9, 70.0)
    assert choose_best([good, thin, losing], 100000.0, 3) is good
    assert choose_best([thin, losing], 100000.0, 3) is None


def test_sharp_peak_is_flagged():
    from algoverse.backtest.robustness import peak_check

    peak = _result([100.0, 50.0, 120.0], 5, 60.0)
    poor = _result([-100.0, 40.0, -120.0], 5, 55.0)
    assert peak_check(
        [poor, peak, replace(poor, spec=replace(poor.spec, min_score=65.0))], peak, 1e5
    )["sharp_peak"]
    steady = _result([90.0, 55.0, 110.0], 5, 55.0)
    assert not peak_check([steady, peak], peak, 1e5)["sharp_peak"]


def test_report_ids_cannot_escape_the_reports_folder(tmp_path):
    registry = Registry(tmp_path)
    assert registry.read_report("../../etc/passwd") is None


def test_summary_warns_on_small_samples_and_uncovered_stress_windows():
    config = load_backtest_config(CONFIG_DIR)
    trades = stats_trades([100.0, -50.0]).assign(
        profile="debit_spread",
        strategy="x",
        symbol="NIFTY",
        dte_at_entry=5,
        iv_regime="mid",
        trend="up",
    )
    daily = pd.Series([100.0, -50.0], index=[date(2026, 1, 1), date(2026, 1, 2)])
    summary = summarize(trades, daily, config, 500000.0)
    text = " ".join(summary["warnings"])
    assert "Only 2 trades" in text and "one volatility regime" in text and "stress period" in text


def test_backtest_api_empty_and_unknown_report(tmp_path):
    from algoverse.api.app import create_app
    from algoverse.settings import Settings
    from fastapi.testclient import TestClient

    settings = Settings(_env_file=None, data_dir=tmp_path, ai_enabled=False)
    client = TestClient(create_app(settings))
    body = client.get("/api/backtests").json()
    assert body == {"history": {"sessions": 0, "start": None, "end": None}, "reports": []}
    assert client.get("/api/backtests/not-a-report").status_code == 404

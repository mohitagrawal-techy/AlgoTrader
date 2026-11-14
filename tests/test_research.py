"""Research studies, the pair screen, the ML signal, order slicing and 1-minute rules."""

import json
from dataclasses import replace
from datetime import date, datetime, time

import numpy as np
import pandas as pd
import pytest
from algoverse.api.app import create_app
from algoverse.lab.spec import StrategySpec
from algoverse.lab.technical import INTRADAY_MINUTES, Frames, TechRule, rule_failure
from algoverse.research import ml, pairs, studies
from algoverse.research.service import ResearchError, ResearchService
from algoverse.risk.costs import load_cost_model
from algoverse.settings import Settings
from conftest import CONFIG_DIR, IST
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from test_lab import HEADER

# ---- Helpers ----------------------------------------------------------------------------------


def daily_bars(closes, start="2010-01-01", gaps=None):
    days = pd.bdate_range(start, periods=len(closes))
    close = pd.Series(closes, dtype=float)
    previous = close.shift(1).fillna(close.iloc[0])
    opening = previous * (1 + (pd.Series(gaps) if gaps is not None else 0.0))
    return pd.DataFrame(
        {
            "ts": days.tz_localize(IST),
            "open": opening,
            "high": np.maximum(opening, close) * 1.003,
            "low": np.minimum(opening, close) * 0.997,
            "close": close,
        }
    )


def walk(count, seed=1, drift=0.0003, sd=0.01, start=100.0):
    rng = np.random.default_rng(seed)
    return start * np.exp(np.cumsum(rng.normal(drift, sd, count)))


# ---- Studies ----------------------------------------------------------------------------------


def test_time_of_day_buckets_moves_inside_the_session():
    rows = []
    for day in pd.bdate_range("2024-01-01", periods=5):
        ts = pd.date_range(f"{day.date()} 09:15", periods=375, freq="min", tz=IST)
        # Rises 1 point a minute in the first 15 minutes, flat after.
        path = np.concatenate([100 + np.arange(15), np.full(360, 114.0)])
        rows.append(
            pd.DataFrame({"ts": ts, "open": path, "high": path, "low": path, "close": path})
        )
    found = studies.time_of_day(pd.concat(rows))
    first = found["rows"][0]
    assert first["slot"] == "09:15" and first["up_share"] == 1.0 and first["mean_pct"] > 0
    assert found["rows"][-1]["slot"] == "15:15" and found["rows"][-1]["mean_pct"] == 0
    assert found["sessions"] == 5 and len(found["rows"]) == 25
    assert studies.time_of_day(pd.DataFrame())["rows"] == []


def test_implied_above_realised_shows_a_positive_premium():
    closes = studies.daily_closes(daily_bars(walk(300, sd=0.01)))
    implied = pd.Series(30.0, index=closes.index)
    found = studies.iv_vs_rv(closes, implied)
    assert found["mean_premium"] > 0 and found["premium_positive_share"] > 0.9
    # A decimal series (ATM IV) is read as percent.
    decimal = studies.iv_vs_rv(closes, implied / 100)
    assert decimal["mean_premium"] == pytest.approx(found["mean_premium"])


def test_correlations_and_rolling_correlation():
    a = studies.daily_closes(daily_bars(walk(300, seed=2)))
    b = a * 1.5
    found = studies.correlations(
        {"A": a, "B": b, "C": studies.daily_closes(daily_bars(walk(300, seed=9)))}
    )
    assert found["names"] == ["A", "B", "C"]
    assert found["matrix"][0][1] == pytest.approx(1.0)
    assert abs(found["matrix"][0][2]) < 0.3
    assert studies.rolling_correlation(a, b)[-1]["corr"] == pytest.approx(1.0)
    assert studies.correlations({"A": a.iloc[:5]})["names"] == []


def test_gap_fill_and_hold():
    closes = [100, 101, 101, 101, 99]
    gaps = [0, 0.02, 0.0, -0.02, 0]
    bars = daily_bars(closes, gaps=gaps)
    found = studies.gaps(bars)
    up = next(r for r in found["rows"] if r["label"] == "Up more than 1%")
    # Opened at 102 over a 100 close, low 100.7: no fill, closed above 100: held.
    assert up["sessions"] == 1 and up["filled_share"] == 0 and up["held_share"] == 1
    down = next(r for r in found["rows"] if r["label"] == "Down more than 1%")
    assert down["sessions"] == 1 and down["filled_share"] == 1


def test_gap_fill_time_from_minutes():
    bars = daily_bars([100, 100], gaps=[0, 0.01])
    day = pd.Timestamp(bars["ts"].iloc[1]).date()
    ts = pd.date_range(f"{day} 09:15", periods=60, freq="min", tz=IST)
    path = np.linspace(101, 99.5, 60)
    minutes = pd.DataFrame({"ts": ts, "open": path, "high": path, "low": path, "close": path})
    row = next(r for r in studies.gaps(bars, minutes)["rows"] if r["sessions"])
    assert 20 <= row["median_fill_minutes"] <= 45


# ---- Pairs ------------------------------------------------------------------------------------


def test_cointegrated_pair_is_found_and_tested_walk_forward():
    rng = np.random.default_rng(4)
    base = np.cumsum(rng.normal(0, 0.01, 1500))
    noise = np.zeros(1500)
    for i in range(1, 1500):
        noise[i] = 0.9 * noise[i - 1] + rng.normal(0, 0.004)
    index = pd.bdate_range("2015-01-01", periods=1500)
    a = pd.Series(np.exp(4.6 + base + noise), index=index)
    b = pd.Series(np.exp(4.0 + base), index=index)
    found = pairs.pair_study(a, b, ("A", "B"))
    assert found["cointegrated_at"] == "1%"
    assert 3 < found["half_life_sessions"] < 15
    assert found["test"]["trades"] > 0 and found["train"]["sessions"] > found["test"]["sessions"]
    unrelated = pd.Series(np.exp(np.cumsum(rng.normal(0, 0.01, 1500))) * 100, index=index)
    loose = pairs.pair_study(unrelated, b, ("U", "B"))
    assert loose["cointegrated_at"] is None
    assert pairs.pair_study(a.iloc[:100], b.iloc[:100], ("A", "B"))["error"]


def test_adf_and_half_life_edges():
    assert pairs.adf_t(np.ones(10)) is None
    trending = np.arange(200, dtype=float)
    assert pairs.half_life(trending) is None


# ---- ML ---------------------------------------------------------------------------------------


def _ml_frame(count=1300, seed=3, edge=0.0):
    """An index whose next move follows yesterday's gap when `edge` is above zero."""
    rng = np.random.default_rng(seed)
    gaps = rng.normal(0, 0.004, count)
    moves = rng.normal(0.0002, 0.008, count)
    moves[1:] += edge * np.sign(gaps[:-1]) * 0.006
    closes = 100 * np.exp(np.cumsum(moves))
    bars = daily_bars(closes, gaps=gaps)
    vix = pd.Series(15 + rng.normal(0, 1, count), index=studies.daily_closes(bars).index)
    return ml.features(bars, vix)


def test_features_use_only_the_session_and_before():
    frame = _ml_frame(400)
    changed = frame.copy()
    bars_a = daily_bars(walk(400, seed=5))
    bars_b = bars_a.copy()
    bars_b.loc[bars_b.index[-1], ["close", "high"]] *= 1.1
    a, b = ml.features(bars_a), ml.features(bars_b)
    # Changing the last session changes only the last row.
    pd.testing.assert_frame_equal(a.iloc[:-1], b.iloc[:-1])
    assert set(ml.FEATURE_LABELS) >= set(changed.columns) - {"close"}


def test_walk_forward_keeps_a_gap_before_each_block():
    study = ml.walk_forward(_ml_frame(), min_train=600, block=100)
    for fold in study["folds"]:
        gap = pd.bdate_range(fold["train_end"], fold["test_start"])
        assert len(gap) - 2 >= study["horizon"] + 5 - 1
    assert study["sessions_tested"] > 500 and not np.isnan(study["ml"]["accuracy"])
    assert 0 <= study["latest"]["prob_up"] <= 1
    assert {r["name"] for r in study["rules"]} >= {"Always long"}
    json.dumps(study)


def test_a_real_edge_is_adopted_and_noise_is_not():
    noise = ml.walk_forward(_ml_frame(edge=0.0), min_train=600, block=100)
    assert not noise["adopted"]
    edge = ml.walk_forward(_ml_frame(edge=1.0), min_train=600, block=100)
    assert edge["ml"]["auc"] > 0.6 and edge["adopted"]
    assert edge["importance"][0]["feature"] in ("Opening gap, %", "1-session return, %")


def test_too_little_history_is_refused():
    assert "error" in ml.walk_forward(_ml_frame(300))


def test_signal_reads_only_what_was_known(tmp_path):
    study = {"adopted": True, "probabilities": {"2026-03-02": 0.7, "2026-03-03": 0.2}}
    ml.save_signal(tmp_path, "NIFTY", study)
    ml.activate(tmp_path, ["NIFTY", "BANKNIFTY"])
    # During 3 March, only 2 March's reading is known; after the close, 3 March's own.
    assert ml.probability("NIFTY", date(2026, 3, 3), after_close=False) == 0.7
    assert ml.probability("NIFTY", date(2026, 3, 3), after_close=True) == 0.2
    assert ml.probability("NIFTY", date(2026, 3, 2), after_close=False) is None
    assert ml.probability("BANKNIFTY", date(2026, 3, 3), after_close=True) is None


def test_lab_strategies_can_use_the_ml_signal():
    spec = StrategySpec.model_validate(
        {
            "id": "s1",
            "name": "ML calls",
            "legs": [{"action": "buy", "option_type": "CE", "select": "delta", "value": 0.4}],
            "entry": {"conditions": [{"signal": "ml_prob_up", "op": "above", "value": 0.6}]},
        }
    )
    assert spec.entry.conditions[0].signal == "ml_prob_up"


# ---- Research service and API -----------------------------------------------------------------


def _store_candles(data_dir, name, bars):
    from algoverse.data.store import SnapshotStore

    SnapshotStore(data_dir).upsert_candles(name, bars)


def test_service_builds_every_study_and_runs_ml(tmp_path):
    for name, seed in (("NIFTY", 1), ("BANKNIFTY", 2), ("FINNIFTY", 3)):
        _store_candles(tmp_path, name, daily_bars(walk(1100, seed=seed)))
    vix = daily_bars(15 + np.abs(walk(1100, seed=8, drift=0) - 100) / 10)
    _store_candles(tmp_path, "INDIAVIX", vix)
    service = ResearchService(tmp_path, CONFIG_DIR)
    found = service.studies("NIFTY")
    assert found["symbols"] == ["NIFTY", "BANKNIFTY", "FINNIFTY"]
    assert found["vix_vs_rv"]["sessions"] > 0 and found["gaps"]["sessions"] == 1099
    assert len(found["pairs"]) == 2 and found["ml"] is None
    assert service.studies("NIFTY") is found
    study = service.run_ml("NIFTY", 1, datetime(2026, 11, 14, tzinfo=IST))
    assert "probabilities" in study
    assert service.studies("NIFTY")["ml"]["sessions_tested"] == study["sessions_tested"]
    assert "probabilities" not in service.studies("NIFTY")["ml"]
    with pytest.raises(ResearchError):
        service.studies("SENSEX")


def test_api(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, ai_enabled=False)
    client = TestClient(create_app(settings))
    assert client.get("/api/research").status_code == 400
    assert client.get("/api/research/job").json() == {"job": None}
    assert client.post("/api/research/ml", json={"symbol": "NIFTY"}).status_code == 403
    bad = client.post("/api/research/ml", json={"symbol": "NIFTY", "horizon": 50}, headers=HEADER)
    assert bad.status_code == 422
    wrong = client.post("/api/research/ml", json={"symbol": "XYZ"}, headers=HEADER)
    assert wrong.status_code == 400


# ---- Order slices at the freeze quantity ------------------------------------------------------


def test_orders_above_the_freeze_quantity_are_split(risk_config):
    assert risk_config.order_slices("NIFTY", 1755) == 1
    assert risk_config.order_slices("NIFTY", 1756) == 2
    assert risk_config.order_slices("BANKNIFTY", 3000) == 4
    assert risk_config.order_slices("OTHER", 10**6) == 1


def test_each_slice_pays_brokerage():
    costs = load_cost_model(CONFIG_DIR)
    one = costs.order(True, 100.0, 1755, date(2026, 11, 13))
    two = costs.order(True, 100.0, 1755, date(2026, 11, 13), orders=2)
    assert two - one == pytest.approx(costs.rates(date(2026, 11, 13)).brokerage_per_order * 1.18)


def test_lots_are_capped_to_the_slice_limit(risk_config):
    from test_paper import candidate, evaluate

    base = evaluate(risk_config).lots
    lot = candidate().legs[0].lot_size
    roomy = risk_config.model_copy(update={"freeze_quantity": {"NIFTY": lot * 100}})
    assert evaluate(roomy).lots == base
    # One lot is more than one order may hold, and only one slice is allowed.
    tight = risk_config.model_copy(
        update={"freeze_quantity": {"NIFTY": lot - 1}, "max_order_slices": 1}
    )
    refused = evaluate(tight)
    assert not refused.approved and "order slice limit" in refused.reasons[0]
    assert evaluate(tight, cand=replace(candidate(), symbol="OTHER")).lots == base
    budget = risk_config.model_copy(
        update={
            "size_by_budget": True,
            "max_lots_per_trade": 1000,
            "freeze_quantity": {"NIFTY": lot * 3},
            "max_order_slices": 2,
        }
    )
    assert evaluate(budget).lots == 6


# ---- One-minute rules -------------------------------------------------------------------------


def test_one_minute_rules_read_minute_candles():
    assert INTRADAY_MINUTES["1m"] == 1
    ts = pd.date_range("2026-03-02 09:15", periods=60, freq="min", tz=IST)
    path = np.linspace(100, 160, 60)
    minutes = pd.DataFrame({"ts": ts, "open": path, "high": path, "low": path, "close": path})
    frames = Frames(pd.DataFrame(), minutes)
    assert len(frames.frame("1m")) == 60
    rule = TypeAdapter(TechRule).validate_python(
        {
            "type": "compare",
            "timeframe": "1m",
            "left": {"kind": "indicator", "name": "close"},
            "op": "above",
            "right": {"kind": "indicator", "name": "ema", "params": {"period": 9}},
        }
    )
    assert rule_failure(rule, frames) is None
    spec = StrategySpec.model_validate(
        {
            "id": "s1",
            "name": "Minute momentum",
            "legs": [{"action": "buy", "option_type": "CE", "select": "delta", "value": 0.4}],
            "decide_every": "5m",
            "entry": {"dte_min": 0, "rules": [rule.model_dump()]},
            "exits": {"exit_dte": 0, "square_off": time(15, 15)},
        }
    )
    assert spec.entry.rules[0].timeframe == "1m"
    with pytest.raises(ValueError, match="minutes"):
        StrategySpec.model_validate(
            {
                **spec.model_dump(),
                "decide_every": "day",
                "entry": {"dte_min": 3, "rules": [rule.model_dump()]},
                "exits": {"exit_dte": 1},
            }
        )

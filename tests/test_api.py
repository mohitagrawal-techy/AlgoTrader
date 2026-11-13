import json
from datetime import date

import numpy as np
import pytest
from algoverse.api.app import create_app
from algoverse.brokers.auth import StoredToken
from algoverse.brokers.base import AuthExpiredError
from algoverse.dashboard import clean
from algoverse.evaluation.config import EventCalendar
from algoverse.execution.cycle import run_cycle
from algoverse.runtime import Runtime
from algoverse.settings import Settings
from conftest import NOW, later, make_bars, make_contracts, make_raw_chain
from fastapi.testclient import TestClient

HEADER = {"X-AlgoVerse-Client": "dashboard"}


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, upstox_api_key="key", data_dir=tmp_path, ai_enabled=False)


def seed(settings):
    runtime = Runtime(settings)
    runtime.deps.risk.paper.min_score = 0.0
    runtime.deps.events = EventCalendar(reviewed_through=date(2027, 1, 31))
    runtime.store.upsert_candles("NIFTY", make_bars())
    runtime.store.upsert_candles("INDIAVIX", make_bars(start=14.0, drift=0.0).assign(close=14.0))
    raw = make_raw_chain()
    contracts = make_contracts(raw)
    run_cycle(runtime.deps, {"NIFTY": raw}, contracts, NOW)
    run_cycle(runtime.deps, {"NIFTY": make_raw_chain(now=later(5))}, contracts, later(5))
    return runtime


def test_clean_produces_strict_json():
    value = clean({"a": float("nan"), "b": [np.float64(1.5), float("inf")], "c": date(2026, 1, 2)})
    assert value == {"a": None, "b": [1.5, None], "c": "2026-01-02"}
    json.dumps(value, allow_nan=False)


def test_empty_state_before_any_data(settings):
    client = TestClient(create_app(settings))
    assert client.get("/api/ideas").json() == {"generated_at": None, "ideas": []}
    assert client.get("/api/market").json() == {"generated_at": None, "symbols": {}}
    portfolio = client.get("/api/portfolio").json()
    assert portfolio["live"] == [] and portfolio["equity"] == []
    assert client.get("/api/health").json()["data_as_of"] is None


def test_ideas_payload_explains_every_idea(settings):
    seed(settings)
    ideas = TestClient(create_app(settings)).get("/api/ideas").json()["ideas"]
    assert ideas
    assert len({idea["id"] for idea in ideas}) == len(ideas)
    for idea in ideas:
        assert idea["tradable"] or idea["rejections"]
        assert idea["parts"] and idea["legs"]
        assert len(idea["payoff"]) >= 160
        assert idea["structure"]["expected_move"] > 0


def test_payoff_matches_structure(settings):
    seed(settings)
    ideas = TestClient(create_app(settings)).get("/api/ideas").json()["ideas"]
    spread = next(idea for idea in ideas if idea["strategy"] == "bull_call_debit")
    lowest = min(point[1] for point in spread["payoff"])
    assert lowest == pytest.approx(-spread["structure"]["max_loss"], rel=1e-3)


def test_market_payload(settings):
    seed(settings)
    nifty = TestClient(create_app(settings)).get("/api/market").json()["symbols"]["NIFTY"]
    assert nifty["spot"] == 24000.0
    assert nifty["iv_source"] == "india_vix_proxy"
    assert len(nifty["expiries"]) == 3
    expiry = nifty["expiries"][0]
    assert expiry["rows"] and expiry["rows"][0]["ce"]["oi_change"] is not None
    side = expiry["rows"][len(expiry["rows"]) // 2]["ce"]
    assert side["gamma"] > 0 and side["theta"] < 0 and side["vega"] > 0
    strikes = [row["strike"] for row in expiry["rows"]]
    assert strikes == sorted(strikes)


def test_portfolio_shows_live_position_and_limits(settings):
    seed(settings)
    portfolio = TestClient(create_app(settings)).get("/api/portfolio").json()
    assert len(portfolio["live"]) >= 1
    assert portfolio["live"][0]["front_expiry"]
    assert {limit["key"] for limit in portfolio["limits"]} >= {"daily_loss", "margin", "vega"}
    assert len(portfolio["equity"]) == 2
    assert portfolio["closed_total"] == 0 and portfolio["unfilled_total"] == 0
    assert portfolio["activity"]
    symbols = {row["symbol"] for row in portfolio["by_symbol"]}
    assert symbols == {position["symbol"] for position in portfolio["live"]}
    stress = portfolio["stress"]
    assert len(stress["pnl"]) == len(stress["spot_moves_pct"])
    assert all(len(row) == len(stress["iv_shifts_pct"]) for row in stress["pnl"])
    assert stress["limit"] > 0


def test_refresh_requires_dashboard_header(settings):
    assert TestClient(create_app(settings)).post("/api/refresh").status_code == 403


def test_refresh_reports_expired_session(settings, monkeypatch):
    def expired(*args, **kwargs):
        raise AuthExpiredError("login")

    monkeypatch.setattr("algoverse.api.app.collect_snapshots", expired)
    monkeypatch.setattr(Runtime, "contracts", lambda self, now: None)
    response = TestClient(create_app(settings)).post("/api/refresh", headers=HEADER)
    assert response.status_code == 401


def test_refresh_ranks_without_trading(settings, monkeypatch):
    runtime = seed(settings)
    before = len(runtime.ledger.positions(("pending", "open")))
    raw = make_raw_chain()

    def collected(*args, **kwargs):
        from algoverse.data.collector import CollectResult

        return CollectResult(saved=3, chains={"NIFTY": raw})

    monkeypatch.setattr("algoverse.api.app.collect_snapshots", collected)
    monkeypatch.setattr(Runtime, "contracts", lambda self, now: make_contracts(raw))
    response = TestClient(create_app(settings)).post("/api/refresh", headers=HEADER)
    assert response.status_code == 200 and response.json()["ideas"] > 0
    assert len(runtime.ledger.positions(("pending", "open"))) == before


def test_health_reports_session(settings):
    app = create_app(settings)
    runtime_store = Runtime(settings).tokens
    runtime_store.save(StoredToken("abc", NOW.replace(year=2099)))
    body = TestClient(app).get("/api/health").json()
    assert body["broker_session_valid"] is True
    assert body["upstox_configured"] is True
    assert "abc" not in json.dumps(body)

"""The events calendar: owner events, the review date, and what the risk engine then reads."""

import json
from datetime import date

import pandas as pd
import pytest
from algoverse.ai.event_store import load_live_calendar
from algoverse.api.app import create_app
from algoverse.market.calendar_events import (
    CalendarError,
    NewEvent,
    OwnerEvents,
    calendar_view,
)
from algoverse.settings import Settings
from conftest import CONFIG_DIR
from fastapi.testclient import TestClient
from test_lab import HEADER

TODAY = date(2026, 11, 14)
SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY"]


def test_owner_events_are_added_listed_and_deleted(tmp_path):
    store = OwnerEvents(tmp_path)
    added = store.add(NewEvent(date=date(2026, 12, 5), name="RBI policy"), SYMBOLS)
    store.add(NewEvent(date=date(2026, 12, 1), name="GDP", severity="medium"), SYMBOLS)
    assert [e.name for e in store.events()] == ["GDP", "RBI policy"]
    store.delete(added.id)
    assert [e.name for e in store.events()] == ["GDP"]
    with pytest.raises(CalendarError):
        store.delete(added.id)
    with pytest.raises(CalendarError, match="Unknown underlying"):
        store.add(NewEvent(date=TODAY, name="X", symbols=["NOPE"]), SYMBOLS)


def test_the_risk_engine_reads_owner_events_and_the_review_date(tmp_path):
    store = OwnerEvents(tmp_path)
    store.add(NewEvent(date=date(2026, 12, 5), name="RBI policy", symbols=["NIFTY"]), SYMBOLS)
    calendar = load_live_calendar(CONFIG_DIR, tmp_path, TODAY)
    assert not calendar.is_reviewed(date(2026, 11, 20))
    store.set_reviewed(date(2026, 12, 31))
    calendar = load_live_calendar(CONFIG_DIR, tmp_path, TODAY)
    assert calendar.is_reviewed(date(2026, 12, 20))
    found = calendar.in_window("NIFTY", TODAY, date(2026, 12, 10))
    assert [e.name for e in found] == ["RBI policy"]
    assert calendar.in_window("BANKNIFTY", TODAY, date(2026, 12, 10)) == []
    store.set_reviewed(None)
    assert load_live_calendar(CONFIG_DIR, tmp_path, TODAY).reviewed_through is None


def test_view_merges_every_source_in_date_order(tmp_path):
    OwnerEvents(tmp_path).add(NewEvent(date=date(2026, 11, 20), name="Fed decision"), SYMBOLS)
    (tmp_path / "market").mkdir(exist_ok=True)
    (tmp_path / "market" / "ai_events.json").write_text(
        json.dumps(
            [
                {
                    "date": "2026-11-30",
                    "name": "MSCI rebalance",
                    "severity": "high",
                    "symbols": ["NIFTY"],
                }
            ]
        )
    )
    (tmp_path / "market_holidays.json").write_text(
        json.dumps(
            [
                {
                    "date": "2026-11-24",
                    "description": "Guru Nanak Jayanti",
                    "closed_exchanges": ["NSE", "NFO"],
                    "open_exchanges": [],
                },
                {
                    "date": "2026-11-08",
                    "description": "Muhurat Trading",
                    "closed_exchanges": [],
                    "open_exchanges": [{"exchange": "NFO", "start_time": 0, "end_time": 0}],
                },
            ]
        )
    )
    contracts = pd.DataFrame(
        {
            "instrument_key": ["a", "b", "c"],
            "underlying_symbol": ["NIFTY", "NIFTY", "BANKNIFTY"],
            "expiry": pd.to_datetime(["2026-11-17", "2026-11-24", "2026-11-24"]),
            "weekly": [True, False, False],
        }
    )
    folder = tmp_path / "options" / "contracts" / "date=2026-11-13"
    folder.mkdir(parents=True)
    contracts.to_parquet(folder / "part.parquet")
    view = calendar_view(
        CONFIG_DIR, tmp_path, SYMBOLS, date(2026, 11, 1), date(2026, 11, 30), TODAY
    )
    kinds = [(str(i["date"]), i["kind"]) for i in view["items"]]
    assert kinds == [
        ("2026-11-08", "special"),
        ("2026-11-17", "expiry"),
        ("2026-11-20", "owner"),
        ("2026-11-24", "expiry"),
        ("2026-11-24", "expiry"),
        ("2026-11-24", "holiday"),
        ("2026-11-30", "ai"),
    ]
    names = [i["name"] for i in view["items"]]
    assert "NIFTY weekly expiry" in names and "BANKNIFTY monthly expiry" in names
    with pytest.raises(CalendarError):
        calendar_view(CONFIG_DIR, tmp_path, SYMBOLS, date(2026, 11, 2), date(2026, 11, 1), TODAY)
    with pytest.raises(CalendarError):
        calendar_view(CONFIG_DIR, tmp_path, SYMBOLS, date(2025, 1, 1), date(2026, 11, 1), TODAY)


def test_api(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, ai_enabled=False)
    client = TestClient(create_app(settings))
    body = {"date": "2026-12-05", "name": "RBI policy", "severity": "high", "symbols": []}
    assert client.post("/api/calendar/events", json=body).status_code == 403
    added = client.post("/api/calendar/events", json=body, headers=HEADER).json()
    view = client.get("/api/calendar?start=2026-12-01&end=2026-12-31").json()
    assert [i["name"] for i in view["items"] if i["kind"] == "owner"] == ["RBI policy"]
    assert view["reviewed_through"] is None
    reviewed = client.put("/api/calendar/reviewed", json={"through": "2026-12-31"}, headers=HEADER)
    assert reviewed.json() == {"reviewed_through": "2026-12-31"}
    assert (
        client.get("/api/calendar?start=2026-12-01&end=2026-12-31").json()["reviewed_through"]
        == "2026-12-31"
    )
    gone = client.delete(f"/api/calendar/events/{added['id']}", headers=HEADER)
    assert gone.status_code == 200
    assert client.delete(f"/api/calendar/events/{added['id']}", headers=HEADER).status_code == 400
    bad = client.post("/api/calendar/events", json={**body, "name": ""}, headers=HEADER)
    assert bad.status_code == 422
    assert client.get("/api/calendar?start=2026-12-02&end=2026-12-01").status_code == 400

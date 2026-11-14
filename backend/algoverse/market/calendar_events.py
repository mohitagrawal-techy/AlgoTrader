"""The events calendar: events the owner adds from the dashboard, alongside the ones in
events.yaml, the ones the AI news brief found, market holidays and option expiries.

Owner events live in the data folder, because config/ is mounted read-only in Docker. Together
with events.yaml and the AI's events they make the live calendar the risk engine reads: short
premium is blocked around high-severity events, and whenever a trade would be held past the
date the owner has reviewed the calendar through.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from algoverse.ai.event_store import AiEventStore
from algoverse.data import options
from algoverse.data.holidays import OPTIONS_EXCHANGE, _read_rows, holidays_path
from algoverse.evaluation.config import Event, EventCalendar, load_event_calendar

MAX_OWNER_EVENTS = 500
MAX_SPAN_DAYS = 400


class OwnerEvent(Event):
    id: str
    note: str = Field(default="", max_length=300)


class NewEvent(BaseModel):
    date: date
    name: str = Field(min_length=1, max_length=80)
    severity: Literal["high", "medium"] = "high"
    symbols: list[str] = Field(default_factory=list, max_length=10)
    note: str = Field(default="", max_length=300)


class CalendarError(Exception):
    pass


class OwnerEvents:
    """Events and the reviewed-through date, set from the dashboard."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "market" / "owner_events.json"

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return {"reviewed_through": None, "events": []}

    def _write(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, default=str))
        os.replace(tmp, self.path)

    def events(self) -> list[OwnerEvent]:
        return [OwnerEvent.model_validate(row) for row in self._read().get("events", [])]

    def reviewed_through(self) -> date | None:
        value = self._read().get("reviewed_through")
        return date.fromisoformat(value) if value else None

    def add(self, event: NewEvent, symbols: list[str]) -> OwnerEvent:
        unknown = [s for s in event.symbols if s not in symbols]
        if unknown:
            raise CalendarError(f"Unknown underlying: {', '.join(unknown)}.")
        payload = self._read()
        if len(payload.get("events", [])) >= MAX_OWNER_EVENTS:
            raise CalendarError(f"The calendar holds at most {MAX_OWNER_EVENTS} events.")
        added = OwnerEvent(id=secrets.token_hex(4), **event.model_dump())
        payload.setdefault("events", []).append(added.model_dump(mode="json"))
        payload["events"].sort(key=lambda row: row["date"])
        self._write(payload)
        return added

    def delete(self, event_id: str) -> None:
        payload = self._read()
        kept = [row for row in payload.get("events", []) if row["id"] != event_id]
        if len(kept) == len(payload.get("events", [])):
            raise CalendarError("That event no longer exists.")
        payload["events"] = kept
        self._write(payload)

    def set_reviewed(self, through: date | None) -> None:
        payload = self._read()
        payload["reviewed_through"] = through.isoformat() if through else None
        self._write(payload)


def live_calendar(config_dir: Path, data_dir: Path, today: date) -> EventCalendar:
    """events.yaml, the owner's dashboard events and the AI's events, as the risk engine reads
    them. The calendar counts as reviewed through the later of the two owner dates."""
    configured = load_event_calendar(config_dir)
    owner = OwnerEvents(data_dir)
    dates = [d for d in (configured.reviewed_through, owner.reviewed_through()) if d]
    events = [
        *configured.events,
        *(
            Event(**e.model_dump(include={"date", "name", "severity", "symbols"}))
            for e in owner.events()
        ),
        *AiEventStore(data_dir).load(today),
    ]
    return EventCalendar(reviewed_through=max(dates) if dates else None, events=events)


def _expiries(data_dir: Path, symbols: list[str], start: date, end: date, today: date) -> list:
    """Listed option expiries in the span, from the latest contract master."""
    contracts = options.read_contracts(Path(data_dir), today)
    if contracts is None or contracts.empty:
        return []
    rows = contracts[contracts["underlying_symbol"].isin(symbols)]
    found = []
    for (symbol, expiry), group in rows.groupby(["underlying_symbol", "expiry"]):
        day = pd.Timestamp(expiry).date()
        if not start <= day <= end:
            continue
        weekly = bool(group["weekly"].any()) if "weekly" in group else False
        found.append(
            {
                "date": day,
                "kind": "expiry",
                "name": f"{symbol} {'weekly' if weekly else 'monthly'} expiry",
                "symbols": [symbol],
                "severity": None if weekly else "medium",
            }
        )
    return found


def _holidays(data_dir: Path, start: date, end: date) -> list:
    found = []
    for row in _read_rows(holidays_path(Path(data_dir))):
        day = date.fromisoformat(row["date"])
        if not start <= day <= end:
            continue
        closed = OPTIONS_EXCHANGE in row.get("closed_exchanges", [])
        found.append(
            {
                "date": day,
                "kind": "holiday" if closed else "special",
                "name": row.get("description")
                or ("Market holiday" if closed else "Special session"),
                "symbols": [],
                "severity": None,
            }
        )
    return found


def calendar_view(
    config_dir: Path, data_dir: Path, symbols: list[str], start: date, end: date, today: date
) -> dict:
    """Everything dated between `start` and `end`, each item tagged with where it came from."""
    if end < start:
        raise CalendarError("The end date is before the start date.")
    if (end - start).days > MAX_SPAN_DAYS:
        raise CalendarError(f"Ask for at most {MAX_SPAN_DAYS} days at a time.")
    configured = load_event_calendar(config_dir)
    owner = OwnerEvents(data_dir)
    items: list[dict] = []
    for event in owner.events():
        if start <= event.date <= end:
            items.append(event.model_dump() | {"kind": "owner"})
    for event in configured.events:
        if start <= event.date <= end:
            items.append(event.model_dump() | {"kind": "config"})
    for event in AiEventStore(data_dir).load(min(start, today)):
        if start <= event.date <= end:
            items.append(event.model_dump() | {"kind": "ai"})
    items += _holidays(data_dir, start, end)
    items += _expiries(data_dir, symbols, start, end, today)
    items.sort(key=lambda item: (item["date"], item["kind"], item["name"]))
    live = live_calendar(config_dir, data_dir, today)
    return {
        "start": start,
        "end": end,
        "today": today,
        "symbols": symbols,
        "items": items,
        "reviewed_through": live.reviewed_through,
        "reviewed_sources": {
            "config": configured.reviewed_through,
            "dashboard": owner.reviewed_through(),
        },
        # The risk engine blocks short premium held past the reviewed date.
        "short_premium_open_until": live.reviewed_through,
        "next_unreviewed": (live.reviewed_through + timedelta(days=1))
        if live.reviewed_through
        else today,
    }

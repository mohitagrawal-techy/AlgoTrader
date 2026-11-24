"""Events the news brief found, kept apart from the calendar the owner maintains."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from algoverse.evaluation.config import Event, EventCalendar

DATE_FORMATS = ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y")
SEVERITIES = {"high", "medium"}
MAX_DAYS_AHEAD = 60
MAX_EVENTS = 40


def parse_event_date(text: str | None, today: date) -> date | None:
    """A clearly stated date in the near future, or None. Vague wording is never guessed."""
    value = (text or "").strip()
    for layout in DATE_FORMATS:
        try:
            parsed = datetime.strptime(value, layout).date()
        except ValueError:
            continue
        return parsed if today <= parsed <= today + timedelta(days=MAX_DAYS_AHEAD) else None
    return None


def is_calendar_event(impact: str, text: str | None, today: date) -> bool:
    return impact in SEVERITIES and parse_event_date(text, today) is not None


class AiEventStore:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "market" / "ai_events.json"

    def load(self, today: date) -> list[Event]:
        try:
            rows = json.loads(self.path.read_text())
        except FileNotFoundError:
            return []
        events = [Event.model_validate(row) for row in rows]
        return [event for event in events if event.date >= today]

    def add(self, symbol: str, brief_events: list[dict], today: date) -> None:
        """Merge a brief's dated events. Repeating a brief changes nothing."""
        kept = self.load(today)
        known = {(e.date, e.name.lower(), tuple(e.symbols)) for e in kept}
        added = False
        for item in brief_events:
            when = parse_event_date(item.get("date"), today)
            if when is None or item.get("impact") not in SEVERITIES:
                continue
            event = Event(date=when, name=item["name"], severity=item["impact"], symbols=[symbol])
            key = (event.date, event.name.lower(), tuple(event.symbols))
            if key not in known and len(kept) < MAX_EVENTS:
                known.add(key)
                kept.append(event)
                added = True
        if added:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps([event.model_dump(mode="json") for event in kept]))
            os.replace(tmp, self.path)


def load_live_calendar(config_dir: Path, data_dir: Path, today: date) -> EventCalendar:
    """events.yaml, the events the owner adds from the dashboard, and AI-found events. Only the
    owner can set reviewed_through, in events.yaml or on the dashboard's calendar."""
    from algoverse.market.calendar_events import live_calendar

    return live_calendar(config_dir, data_dir, today)

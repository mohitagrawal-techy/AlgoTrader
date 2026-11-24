"""NSE F&O holidays and special sessions from Upstox, saved so they survive restarts.

Upstox lists settlement holidays and special sessions here too, and F&O trades on those days, so
each date is read by whether NFO is closed or open and not by its holiday type.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from algoverse.brokers.base import MarketDataError
from algoverse.brokers.upstox import BASE_URL

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
HOLIDAYS_PATH = "/v2/market/holidays"
OPTIONS_EXCHANGE = "NFO"
MAX_AGE_SECONDS = 12 * 3600

Sessions = dict[date, tuple[time, time]]


def fetch_holidays(client: httpx.Client | None = None) -> list[dict]:
    """This year's list. Upstox serves it without a login, so it works before the daily login."""
    http = client or httpx.Client(base_url=BASE_URL, timeout=20)
    try:
        response = http.get(HOLIDAYS_PATH, headers={"Accept": "application/json"})
    finally:
        if client is None:
            http.close()
    if response.status_code != 200:
        raise MarketDataError(f"Upstox holidays returned {response.status_code}")
    body = response.json()
    if body.get("status") != "success" or not isinstance(body.get("data"), list):
        raise MarketDataError("Upstox holidays returned an unexpected response")
    return body["data"]


def _ist_time(milliseconds: int) -> time:
    return datetime.fromtimestamp(milliseconds / 1000, IST).time().replace(tzinfo=None)


def parse_holidays(rows: list[dict]) -> tuple[set[date], Sessions]:
    """Dates F&O is closed, and the hours of every listed date on which it is open."""
    closed: set[date] = set()
    sessions: Sessions = {}
    for row in rows:
        day = date.fromisoformat(row["date"])
        if OPTIONS_EXCHANGE in row.get("closed_exchanges", []):
            closed.add(day)
            continue
        for entry in row.get("open_exchanges", []):
            if entry.get("exchange") == OPTIONS_EXCHANGE:
                sessions[day] = (_ist_time(entry["start_time"]), _ist_time(entry["end_time"]))
    return closed, sessions


def holidays_path(data_dir: Path) -> Path:
    return Path(data_dir) / "market_holidays.json"


def _read_rows(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        log.warning("Ignoring an unreadable holiday file: %s", path)
        return []


def save_holidays(data_dir: Path, rows: list[dict]) -> None:
    """Replace the years in `rows` and keep earlier years, which Upstox stops listing."""
    path = holidays_path(data_dir)
    years = {row["date"][:4] for row in rows}
    kept = [row for row in _read_rows(path) if row["date"][:4] not in years]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(kept + rows, key=lambda row: row["date"])))
    os.replace(tmp, path)


def load_holidays(data_dir: Path) -> tuple[set[date], Sessions]:
    return parse_holidays(_read_rows(holidays_path(data_dir)))


def refresh_holidays(
    data_dir: Path,
    now: datetime,
    fetch: Callable[[], list[dict]] = fetch_holidays,
    max_age_seconds: float = MAX_AGE_SECONDS,
) -> bool:
    """Download the list unless a recent copy exists. Returns whether it downloaded."""
    path = holidays_path(data_dir)
    if path.exists() and now.timestamp() - path.stat().st_mtime < max_age_seconds:
        return False
    save_holidays(data_dir, fetch())
    return True

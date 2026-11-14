"""Load market data from Upstox from the dashboard: what is stored, and jobs that fetch more.

Each job runs the same code as its command-line twin (`algoverse instruments`, `backfill`,
`backfill-minutes`, `history-upstox`, `history-fill`, `holidays`), in a background thread,
one at a time, with its progress lines kept for the page. Every job reads or adds data; none
deletes anything.
"""

from __future__ import annotations

import argparse
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from algoverse.brokers.base import AuthExpiredError
from algoverse.dashboard import clean
from algoverse.data import options
from algoverse.data.candles import MINUTE
from algoverse.data.holidays import holidays_path
from algoverse.data.store import SnapshotStore

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
MAX_LOG_LINES = 400
INVENTORY_SECONDS = 60


@dataclass(frozen=True)
class Action:
    key: str
    label: str
    description: str
    takes_start: bool = False
    plan_only: bool = False


ACTIONS = {
    action.key: action
    for action in (
        Action(
            "instruments",
            "Refresh the instrument master",
            "Today's option contracts, lot sizes and freeze quantities.",
        ),
        Action(
            "daily",
            "Daily index candles",
            "Daily candles of the indices and reference indices, from the start date "
            "(Upstox has them from 2000) or the last ten years.",
            takes_start=True,
        ),
        Action(
            "minutes",
            "One-minute index candles",
            "One-minute candles of the indices, month by month from the start date. Complete "
            "months are skipped, so a re-run picks up where it stopped.",
            takes_start=True,
        ),
        Action(
            "expired",
            "Expired option contracts",
            "Daily history of every expiry Upstox still serves. Needs Upstox Plus. Finished "
            "expiries are skipped on a re-run; a first run can take hours.",
        ),
        Action(
            "fill_plan",
            "Count missing option sessions",
            "Lists the expiries missing from the option history. Fetches nothing.",
            plan_only=True,
        ),
        Action(
            "fill",
            "Fill holes in the option history",
            "Fetches missing sessions, expired contracts and 5-minute option bars. Needs "
            "Upstox Plus for expired contracts.",
        ),
        Action(
            "holidays",
            "Market holidays",
            "NSE F&O holidays and special sessions.",
        ),
    )
}


def _run(key: str, start: date | None, out: Callable[[str], None]) -> None:
    """Run one action with the command line's own code."""
    from algoverse import cli

    args = argparse.Namespace(start=start, plan=key == "fill_plan")
    handlers = {
        "instruments": cli.cmd_instruments,
        "daily": cli.cmd_backfill,
        "minutes": cli.cmd_backfill_minutes,
        "expired": cli.cmd_history_upstox,
        "fill_plan": cli.cmd_history_fill,
        "fill": cli.cmd_history_fill,
        "holidays": cli.cmd_holidays,
    }
    handlers[key](args, out=out)


class DataJobs:
    def __init__(self, data_dir: Path, config_dir: Path, symbols: list[str], references: dict):
        self.data_dir = Path(data_dir)
        self.config_dir = Path(config_dir)
        self.symbols = symbols
        self.references = references
        self.job: dict | None = None
        self._lock = threading.Lock()
        self._inventory: tuple[float, dict] | None = None
        self.runner: Callable[[str, date | None, Callable[[str], None]], None] = _run

    # ---- Jobs ------------------------------------------------------------------------------

    def start(self, key: str, start: date | None, session_valid: bool) -> dict:
        action = ACTIONS.get(key)
        if action is None:
            raise ValueError(f"Unknown action: {key}.")
        if not session_valid and key != "holidays":
            raise ValueError("Log in to Upstox first: the session has expired or was never set.")
        if start is not None and not action.takes_start:
            raise ValueError(f"{action.label} takes no start date.")
        with self._lock:
            if self.job and self.job["state"] == "running":
                raise RuntimeError("A data job is already running. Wait for it to finish.")
            job = {
                "kind": key,
                "label": action.label,
                "state": "running",
                "message": "Starting",
                "started_at": datetime.now(IST).isoformat(),
                "finished_at": None,
                "log": [],
            }
            self.job = job

        def out(*parts) -> None:
            line = " ".join(str(part) for part in parts)
            job["log"].append(f"{datetime.now(IST):%H:%M:%S}  {line}")
            del job["log"][:-MAX_LOG_LINES]
            job["message"] = line[:200]

        def work() -> None:
            try:
                self.runner(key, start, out)
                job["state"] = "done"
                job["message"] = job["log"][-1].split("  ", 1)[-1] if job["log"] else "Done."
            except AuthExpiredError:
                job["state"] = "failed"
                job["message"] = "The Upstox session expired. Log in again and re-run."
            except SystemExit as exc:
                # The command line's own explanation of why it stopped.
                job["state"] = "failed"
                job["message"] = str(exc.code or "Stopped.")
            except Exception as exc:
                log.exception("Data job %s failed", key)
                job["state"] = "failed"
                job["message"] = f"Failed: {exc}"[:300]
            job["finished_at"] = datetime.now(IST).isoformat()
            self._inventory = None

        threading.Thread(target=work, daemon=True).start()
        return job

    # ---- What is stored --------------------------------------------------------------------

    def inventory(self) -> dict:
        """Coverage of every dataset the app uses, cached for a minute."""
        cached = self._inventory
        if cached and time.monotonic() - cached[0] < INVENTORY_SECONDS:
            return cached[1]
        built = clean(self._build())
        self._inventory = (time.monotonic(), built)
        return built

    def _candles(self, store: SnapshotStore, name: str, interval: str) -> dict:
        try:
            frame = store.read_candles(name, interval)
        except (KeyError, ValueError, FileNotFoundError):
            frame = pd.DataFrame()
        if frame.empty:
            return {"name": name, "first": None, "last": None, "sessions": 0}
        days = pd.to_datetime(frame["ts"])
        if days.dt.tz is not None:
            days = days.dt.tz_convert(IST)
        days = days.dt.date
        return {
            "name": name,
            "first": days.min(),
            "last": days.max(),
            "sessions": int(days.nunique()),
        }

    def _build(self) -> dict:
        store = SnapshotStore(self.data_dir, self.references)
        names = [*self.symbols, *self.references]
        history = self.data_dir / "history" / "fo"
        sessions = sorted(date.fromisoformat(p.stem) for p in history.glob("*.parquet"))
        intraday: dict[str, set[date]] = {symbol: set() for symbol in self.symbols}
        for folder in sorted((history / "intraday").glob("date=*")):
            day = date.fromisoformat(folder.name.split("=", 1)[1])
            for path in folder.glob("*.parquet"):
                symbol = path.stem.split("_", 1)[0].upper()
                if symbol in intraday:
                    intraday[symbol].add(day)
        contract_days = sorted(
            options._folder_day(folder)
            for folder in options.contracts_root(self.data_dir).glob("date=*")
        )
        chains = sorted(
            folder.name.split("=", 1)[1]
            for folder in (self.data_dir / "options" / "chains").glob("*/*/date=*")
        )
        holidays = holidays_path(self.data_dir)
        return {
            "daily": [self._candles(store, name, "1d") for name in names],
            "minutes": [self._candles(store, n, MINUTE) for n in names],
            "options_daily": {
                "first": sessions[0] if sessions else None,
                "last": sessions[-1] if sessions else None,
                "sessions": len(sessions),
            },
            "options_intraday": [
                {
                    "name": symbol,
                    "first": min(days) if days else None,
                    "last": max(days) if days else None,
                    "sessions": len(days),
                }
                for symbol, days in intraday.items()
            ],
            "contracts_on": contract_days[-1] if contract_days else None,
            "chain_days": len(set(chains)),
            "chains_last": max(chains) if chains else None,
            "holidays_updated": datetime.fromtimestamp(holidays.stat().st_mtime, IST)
            if holidays.exists()
            else None,
            "actions": [action.__dict__ for action in ACTIONS.values()],
        }

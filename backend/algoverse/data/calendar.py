"""NSE session times."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from algoverse.data.holidays import Sessions, holidays_path, load_holidays

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


class MarketCalendar:
    def __init__(
        self,
        holidays: Iterable[date] = (),
        sessions: Mapping[date, tuple[time, time]] | None = None,
    ):
        self.holidays = set(holidays)
        self.sessions: Sessions = dict(sessions or {})

    def session(self, day: date) -> tuple[time, time] | None:
        """Opening and closing time, or None when F&O does not trade that day."""
        if day in self.holidays:
            return None
        if day in self.sessions:
            return self.sessions[day]
        return (MARKET_OPEN, MARKET_CLOSE) if day.weekday() < 5 else None

    def is_trading_day(self, day: date) -> bool:
        return self.session(day) is not None

    def is_open(self, moment: datetime) -> bool:
        return self.within_session(moment)

    def within_session(
        self, moment: datetime, before: timedelta = timedelta(), after: timedelta = timedelta()
    ) -> bool:
        """Whether a moment is inside today's session, widened by `before` and `after`."""
        local = moment.astimezone(IST)
        hours = self.session(local.date())
        if hours is None:
            return False
        start = datetime.combine(local.date(), hours[0], tzinfo=IST) - before
        end = datetime.combine(local.date(), hours[1], tzinfo=IST) + after
        return start <= local <= end


class BrokerCalendar(MarketCalendar):
    """Follows the saved Upstox list, so long-running services see updates without a restart."""

    def __init__(self, data_dir: Path, manual_holidays: Iterable[date] = ()):
        super().__init__(manual_holidays)
        self._data_dir = Path(data_dir)
        self._manual = set(manual_holidays)
        self._version: int | None = None

    def _sync(self) -> None:
        try:
            version = holidays_path(self._data_dir).stat().st_mtime_ns
        except FileNotFoundError:
            version = None
        if version != self._version:
            closed, sessions = load_holidays(self._data_dir)
            self.holidays = self._manual | closed
            self.sessions = sessions
            self._version = version

    def session(self, day: date) -> tuple[time, time] | None:
        self._sync()
        return super().session(day)

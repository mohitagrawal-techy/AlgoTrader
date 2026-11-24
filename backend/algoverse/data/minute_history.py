"""One-minute history of the underlyings, for intraday rules and the intraday backtester.

Candles are kept in the candle store (candles/interval=1m/.../month=YYYY-MM/part.parquet), one
file per instrument and month. A month is fetched again only while it is unfinished or missing
days, so a re-run after an interruption picks up where it stopped.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from algoverse.brokers.base import AuthExpiredError, MarketDataError
from algoverse.data.candles import MINUTE, CandleStore

log = logging.getLogger(__name__)
IST = "Asia/Kolkata"
# A full NSE session has 375 one-minute candles; a month with fewer days than this share of
# the sessions it should hold is fetched again.
COMPLETE_SHARE = 0.95


@dataclass
class MinuteBackfill:
    fetched: dict[str, int] = field(default_factory=dict)
    kept: dict[str, int] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


def months(start: date, end: date) -> list[tuple[date, date]]:
    """Calendar months covering start to end, clipped to them."""
    spans, first = [], start.replace(day=1)
    while first <= end:
        following = (first + timedelta(days=32)).replace(day=1)
        spans.append((max(first, start), min(following - timedelta(days=1), end)))
        first = following
    return spans


def _days_held(store: CandleStore, key: str, span: tuple[date, date]) -> int:
    held = store.read(key, MINUTE, start=span[0], end=span[1])
    if held.empty:
        return 0
    return pd.to_datetime(held["ts"]).dt.tz_convert(IST).dt.date.nunique()


def backfill_minutes(
    market,
    store: CandleStore,
    keys: dict[str, str],
    start: date,
    today: date,
    sessions: Callable[[date, date], int],
    progress: Callable[[str], None] = lambda _message: None,
) -> MinuteBackfill:
    """Fetch one-minute candles for each instrument key, month by month, up to yesterday.

    `sessions(first, last)` counts trading sessions in a span, so a complete month is skipped.
    """
    result = MinuteBackfill()
    end = today - timedelta(days=1)
    for name, key in keys.items():
        fetched = kept = 0
        for span in months(start, end):
            expected = sessions(*span)
            if expected == 0:
                continue
            if _days_held(store, key, span) >= expected * COMPLETE_SHARE:
                kept += 1
                continue
            try:
                candles = market.minute_range(key, *span)
            except AuthExpiredError:
                raise
            except MarketDataError as exc:
                result.failed[f"{name} {span[0]:%Y-%m}"] = str(exc)
                continue
            if not candles.empty:
                store.write(key, candles, MINUTE)
            fetched += 1
            progress(f"{name} {span[0]:%Y-%m}: {len(candles)} candles")
        result.fetched[name] = fetched
        result.kept[name] = kept
    return result

"""Collects chain snapshots and daily history."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from algoverse.brokers.base import AuthExpiredError, MarketData, MarketDataError
from algoverse.data.instruments import nearest_expiries, underlying_key
from algoverse.data.store import SnapshotStore
from algoverse.settings import Universe

log = logging.getLogger(__name__)
BACKFILL_DAYS = 3650  # Upstox serves at most one decade of daily bars per request.


@dataclass
class CollectResult:
    saved: int = 0
    failures: dict[str, str] = field(default_factory=dict)
    chains: dict[str, pd.DataFrame] = field(default_factory=dict)


def collect_snapshots(
    market: MarketData,
    store: SnapshotStore,
    universe: Universe,
    contracts: pd.DataFrame,
    now: datetime,
) -> CollectResult:
    """Save option chains. One failing chain does not stop the others.

    Every listed expiry is saved when `save_all_expiries` is on, but only the nearest
    `expiries_per_underlying` are returned for analysis, so ideas are unchanged.
    """
    result = CollectResult()
    for symbol in universe.underlyings:
        try:
            key = underlying_key(contracts, symbol)
            analysed = nearest_expiries(
                contracts, symbol, now.date(), universe.expiries_per_underlying
            )
            expiries = (
                nearest_expiries(contracts, symbol, now.date(), None)
                if universe.save_all_expiries
                else analysed
            )
        except ValueError as exc:
            result.failures[symbol] = str(exc)
            log.warning("Skipping %s: %s", symbol, exc)
            continue
        frames = []
        for expiry in expiries:
            try:
                chain = market.option_chain(key, expiry)
            except AuthExpiredError:
                raise
            except MarketDataError as exc:
                result.failures[f"{symbol}:{expiry}"] = str(exc)
                log.warning("Chain failed for %s %s: %s", symbol, expiry, exc)
                continue
            if chain.empty:
                result.failures[f"{symbol}:{expiry}"] = "empty chain"
                continue
            store.write_chain(symbol, now, chain, expiry.isoformat())
            if expiry in analysed:
                frames.append(chain)
            result.saved += 1
        if frames:
            result.chains[symbol] = pd.concat(frames, ignore_index=True)
    return result


def backfill_daily(
    market: MarketData,
    store: SnapshotStore,
    universe: Universe,
    contracts: pd.DataFrame,
    today: date,
    start: date | None = None,
) -> CollectResult:
    """Refresh daily bars for underlyings and reference indices, from `start` when given
    (Upstox has index candles from 2000), else for the last decade."""
    keys = dict(universe.reference_indices)
    result = CollectResult()
    for symbol in universe.underlyings:
        try:
            keys[symbol] = underlying_key(contracts, symbol)
        except ValueError as exc:
            result.failures[symbol] = str(exc)
    first = start or today - timedelta(days=BACKFILL_DAYS)
    spans = []
    while first <= today:
        last = min(today, first + timedelta(days=BACKFILL_DAYS - 1))
        spans.append((first, last))
        first = last + timedelta(days=1)
    for symbol, key in keys.items():
        parts = []
        try:
            for span_start, span_end in spans:
                parts.append(market.daily_candles(key, span_start, span_end))
        except AuthExpiredError:
            raise
        except MarketDataError as exc:
            result.failures[symbol] = str(exc)
            log.warning("Backfill failed for %s: %s", symbol, exc)
            continue
        parts = [part for part in parts if not part.empty]
        candles = (
            pd.concat(parts, ignore_index=True).drop_duplicates("ts", keep="last")
            if parts
            else pd.DataFrame()
        )
        if not candles.empty:
            store.upsert_candles(key, candles)
            result.saved += 1
    return result

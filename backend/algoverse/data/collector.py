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
    """Save nearest-expiry chains. One failing chain does not stop the others."""
    result = CollectResult()
    for symbol in universe.underlyings:
        try:
            key = underlying_key(contracts, symbol)
            expiries = nearest_expiries(
                contracts, symbol, now.date(), universe.expiries_per_underlying
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
) -> CollectResult:
    """Refresh daily bars for underlyings and reference indices."""
    keys = dict(universe.reference_indices)
    result = CollectResult()
    for symbol in universe.underlyings:
        try:
            keys[symbol] = underlying_key(contracts, symbol)
        except ValueError as exc:
            result.failures[symbol] = str(exc)
    start = today - timedelta(days=BACKFILL_DAYS)
    for symbol, key in keys.items():
        try:
            candles = market.daily_candles(key, start, today)
        except AuthExpiredError:
            raise
        except MarketDataError as exc:
            result.failures[symbol] = str(exc)
            log.warning("Backfill failed for %s: %s", symbol, exc)
            continue
        if not candles.empty:
            store.upsert_candles(symbol, candles)
            result.saved += 1
    return result

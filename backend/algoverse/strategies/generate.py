"""Builds every candidate structure for an enriched chain."""

from __future__ import annotations

import pandas as pd

from algoverse.strategies import directional, short_premium, spreads, volatility
from algoverse.strategies.base import Candidate, ExpiryView, GenerationConfig

GENERATORS = (directional, spreads, short_premium, volatility)


def generate_candidates(
    symbol: str, chain: pd.DataFrame, config: GenerationConfig
) -> list[Candidate]:
    """Chain must carry enrichment columns and `lot_size`. Expired expiries are skipped."""
    live = chain[chain["T"] > 0]
    views = [ExpiryView(group) for _, group in live.groupby("expiry", sort=True)]
    return [c for module in GENERATORS for c in module.generate(symbol, views, config)]

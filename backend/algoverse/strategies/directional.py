"""Long calls and puts."""

from __future__ import annotations

from collections.abc import Iterator

from algoverse.strategies.base import BUY, Candidate, ExpiryView, GenerationConfig, make_leg


def generate(symbol: str, views: list[ExpiryView], config: GenerationConfig) -> Iterator[Candidate]:
    for view in views:
        for delta in config.long_deltas:
            for option_type, strategy, bias in (
                ("CE", "long_call", "bullish"),
                ("PE", "long_put", "bearish"),
            ):
                row = view.by_delta(option_type, delta)
                if row is not None:
                    legs = (make_leg(row, BUY),)
                    yield Candidate(symbol, strategy, "directional_buy", bias, legs)

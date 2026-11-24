"""Undefined-risk short straddles and strangles."""

from __future__ import annotations

from collections.abc import Iterator

from algoverse.strategies.base import SELL, Candidate, ExpiryView, GenerationConfig, make_leg


def generate(symbol: str, views: list[ExpiryView], config: GenerationConfig) -> Iterator[Candidate]:
    for view in views:
        call = view.atm("CE")
        put = None if call is None else view.at_strike("PE", call["strike"])
        if call is not None and put is not None:
            legs = (make_leg(put, SELL), make_leg(call, SELL))
            yield Candidate(symbol, "short_straddle", "short_premium", "neutral", legs)
        otm_put = view.by_delta("PE", config.strangle_delta)
        otm_call = view.by_delta("CE", config.strangle_delta)
        if otm_put is not None and otm_call is not None and otm_put["strike"] < otm_call["strike"]:
            legs = (make_leg(otm_put, SELL), make_leg(otm_call, SELL))
            yield Candidate(symbol, "short_strangle", "short_premium", "neutral", legs)

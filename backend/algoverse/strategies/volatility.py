"""Long straddles, long strangles and calendars."""

from __future__ import annotations

from collections.abc import Iterator

from algoverse.strategies.base import BUY, SELL, Candidate, ExpiryView, GenerationConfig, make_leg


def generate(symbol: str, views: list[ExpiryView], config: GenerationConfig) -> Iterator[Candidate]:
    for view in views:
        call = view.atm("CE")
        put = None if call is None else view.at_strike("PE", call["strike"])
        if call is not None and put is not None:
            legs = (make_leg(put, BUY), make_leg(call, BUY))
            yield Candidate(symbol, "long_straddle", "long_vol", "neutral", legs)
        otm_put = view.by_delta("PE", config.long_strangle_delta)
        otm_call = view.by_delta("CE", config.long_strangle_delta)
        if otm_put is not None and otm_call is not None and otm_put["strike"] < otm_call["strike"]:
            legs = (make_leg(otm_put, BUY), make_leg(otm_call, BUY))
            yield Candidate(symbol, "long_strangle", "long_vol", "neutral", legs)
    for near, far in zip(views, views[1:], strict=False):
        near_call = near.atm("CE")
        far_call = None if near_call is None else far.at_strike("CE", near_call["strike"])
        if near_call is not None and far_call is not None:
            legs = (make_leg(near_call, SELL), make_leg(far_call, BUY))
            yield Candidate(symbol, "call_calendar", "calendar", "neutral", legs)

"""Defined-risk verticals, iron condors and butterflies."""

from __future__ import annotations

from collections.abc import Iterator

from algoverse.strategies.base import BUY, SELL, Candidate, ExpiryView, GenerationConfig, make_leg


def _vertical(symbol, view, option_type, long_delta, short_delta, strategy, profile, bias):
    long_row = view.by_delta(option_type, long_delta)
    short_row = view.by_delta(option_type, short_delta)
    if long_row is None or short_row is None or long_row["strike"] == short_row["strike"]:
        return None
    legs = (make_leg(long_row, BUY), make_leg(short_row, SELL))
    return Candidate(symbol, strategy, profile, bias, legs)


def _iron_condor(symbol, view, config) -> Candidate | None:
    rows = (
        view.by_delta("PE", config.condor_long_delta),
        view.by_delta("PE", config.condor_short_delta),
        view.by_delta("CE", config.condor_short_delta),
        view.by_delta("CE", config.condor_long_delta),
    )
    if any(row is None for row in rows):
        return None
    strikes = [row["strike"] for row in rows]
    if not strikes[0] < strikes[1] < strikes[2] < strikes[3]:
        return None
    sides = (BUY, SELL, SELL, BUY)
    legs = tuple(make_leg(row, side) for row, side in zip(rows, sides, strict=True))
    return Candidate(symbol, "iron_condor", "credit_spread", "neutral", legs)


def _butterfly(symbol, view, config) -> Candidate | None:
    body = view.atm("CE")
    if body is None:
        return None
    lower = view.offset(body, -config.butterfly_wing_steps)
    upper = view.offset(body, config.butterfly_wing_steps)
    if lower is None or upper is None:
        return None
    if upper["strike"] - body["strike"] != body["strike"] - lower["strike"]:
        return None
    legs = (make_leg(lower, BUY), make_leg(body, SELL, ratio=2), make_leg(upper, BUY))
    return Candidate(symbol, "call_butterfly", "butterfly", "neutral", legs)


def generate(symbol: str, views: list[ExpiryView], config: GenerationConfig) -> Iterator[Candidate]:
    for view in views:
        built = (
            _vertical(
                symbol,
                view,
                "CE",
                config.debit_long_delta,
                config.debit_short_delta,
                "bull_call_debit",
                "debit_spread",
                "bullish",
            ),
            _vertical(
                symbol,
                view,
                "PE",
                config.debit_long_delta,
                config.debit_short_delta,
                "bear_put_debit",
                "debit_spread",
                "bearish",
            ),
            _vertical(
                symbol,
                view,
                "PE",
                config.credit_long_delta,
                config.credit_short_delta,
                "bull_put_credit",
                "credit_spread",
                "bullish",
            ),
            _vertical(
                symbol,
                view,
                "CE",
                config.credit_long_delta,
                config.credit_short_delta,
                "bear_call_credit",
                "credit_spread",
                "bearish",
            ),
            _iron_condor(symbol, view, config),
            _butterfly(symbol, view, config),
        )
        yield from (candidate for candidate in built if candidate is not None)

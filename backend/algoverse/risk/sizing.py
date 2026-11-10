"""Margin estimate per strategy lot. This is an approximation, not exchange SPAN."""

from __future__ import annotations

from algoverse.risk.config import MarginConfig
from algoverse.strategies.base import SELL, Leg


def _uncovered_short_units(legs: tuple[Leg, ...], option_type: str) -> int:
    """Short units that are not fully covered by a long making the pair a debit spread."""
    same = [leg for leg in legs if leg.option_type == option_type]
    shorts = [leg for leg in same if leg.side == SELL]

    def covers(long_leg: Leg) -> bool:
        if option_type == "CE":
            return any(long_leg.strike < short.strike for short in shorts)
        return any(long_leg.strike > short.strike for short in shorts)

    covered = sum(leg.units for leg in same if leg.side != SELL and covers(leg))
    return max(sum(leg.units for leg in shorts) - covered, 0)


def estimate_margin(
    legs: tuple[Leg, ...],
    spot: float,
    entry_debit: float,
    max_loss: float,
    bounded: bool,
    config: MarginConfig,
) -> float:
    """Rupees of capital one lot ties up, for a structure valued at `entry_debit`."""
    shorts = [leg for leg in legs if leg.side == SELL]
    if not shorts:
        return max(entry_debit, 0.0)
    if len({leg.expiry for leg in legs}) > 1:
        notional = sum(leg.units * spot for leg in shorts)
        return notional * config.calendar_pct_of_notional / 100 + max(entry_debit, 0.0)
    if bounded:
        # Only one side of a hedged structure can lose at expiry.
        units = max(_uncovered_short_units(legs, kind) for kind in ("CE", "PE"))
        return max(
            max_loss * config.hedged_max_loss_multiple,
            units * spot * config.hedged_min_pct_of_notional / 100,
        )
    # Short calls and puts cannot both lose at expiry, so only the larger side is margined.
    by_side = [
        sum(leg.units * spot for leg in shorts if leg.option_type == kind) for kind in ("CE", "PE")
    ]
    return max(by_side) * config.short_option_pct_of_notional / 100

"""Strategy building blocks. Candidates describe one lot of a structure."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd
from pydantic import BaseModel

BUY = 1
SELL = -1


class GenerationConfig(BaseModel):
    long_deltas: list[float]
    debit_long_delta: float
    debit_short_delta: float
    credit_short_delta: float
    credit_long_delta: float
    condor_short_delta: float
    condor_long_delta: float
    strangle_delta: float
    long_strangle_delta: float
    butterfly_wing_steps: int


@dataclass(frozen=True)
class Leg:
    instrument_key: str
    expiry: date
    strike: float
    option_type: str
    side: int
    ratio: int
    lot_size: int
    bid: float
    ask: float
    mid: float
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    oi: float
    volume: float
    T: float
    forward: float
    # The leg's own stop loss and profit target ("pct" of its fill price, or "points"), for
    # custom strategies that set them. None when unused.
    stop: float | None = None
    stop_unit: str = "pct"
    target: float | None = None
    target_unit: str = "pct"

    @property
    def units(self) -> int:
        return self.ratio * self.lot_size

    @property
    def entry_price(self) -> float:
        return self.ask if self.side == BUY else self.bid

    @property
    def exit_price_estimate(self) -> float:
        return self.bid if self.side == BUY else self.ask

    @property
    def is_call(self) -> bool:
        return self.option_type == "CE"

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True)
class Candidate:
    symbol: str
    strategy: str
    profile: str
    bias: str  # bullish, bearish or neutral
    legs: tuple[Leg, ...]
    # Which of a custom strategy's cases this came from: 0 is the first.
    case: int = 0
    # Scales the trade's risk budget, for strategies that size by volatility. 1 is unscaled.
    size_scale: float = 1.0
    # Why the entry rules held when it was proposed: each rule with the values it read.
    why: tuple = field(default=(), compare=False)

    @property
    def front_expiry(self) -> date:
        return min(leg.expiry for leg in self.legs)


def make_leg(row: pd.Series, side: int, ratio: int = 1) -> Leg:
    return Leg(
        instrument_key=str(row["instrument_key"]),
        expiry=row["expiry"],
        strike=float(row["strike"]),
        option_type=str(row["option_type"]),
        side=side,
        ratio=ratio,
        lot_size=int(row["lot_size"]),
        bid=float(row["bid"]),
        ask=float(row["ask"]),
        mid=float(row["mid"]),
        iv=float(row["iv"]),
        delta=float(row["delta"]),
        gamma=float(row["gamma"]),
        theta=float(row["theta"]),
        vega=float(row["vega"]),
        oi=float(row["oi"]),
        volume=float(row["volume"]),
        T=float(row["T"]),
        forward=float(row["forward"]),
    )


class ExpiryView:
    """Finds contracts with a usable quote and Greeks in one expiry."""

    def __init__(self, frame: pd.DataFrame):
        usable = frame.dropna(subset=["mid", "iv", "delta"])
        self._sides = {
            side: usable[usable["option_type"] == side].sort_values("strike", ignore_index=True)
            for side in ("CE", "PE")
        }
        self.expiry: date = frame["expiry"].iloc[0]
        self.forward = float(frame["forward"].iloc[0])

    def by_delta(self, option_type: str, delta: float) -> pd.Series | None:
        """Nearest contract to an absolute delta target."""
        side = self._sides[option_type]
        if side.empty:
            return None
        return side.loc[(side["delta"].abs() - delta).abs().idxmin()]

    def by_premium(self, option_type: str, price: float) -> pd.Series | None:
        """The contract whose mid price is nearest `price` rupees."""
        side = self._sides[option_type]
        if side.empty:
            return None
        return side.loc[(side["mid"] - price).abs().idxmin()]

    def atm(self, option_type: str) -> pd.Series | None:
        side = self._sides[option_type]
        if side.empty:
            return None
        return side.loc[(side["strike"] - self.forward).abs().idxmin()]

    def at_strike(self, option_type: str, strike: float) -> pd.Series | None:
        side = self._sides[option_type]
        match = side[side["strike"] == strike]
        return None if match.empty else match.iloc[0]

    def offset(self, row: pd.Series, steps: int) -> pd.Series | None:
        """The contract a number of listed strikes above (+) or below (-) `row`."""
        side = self._sides[row["option_type"]]
        position = side.index[side["strike"] == row["strike"]]
        if len(position) == 0:
            return None
        target = int(position[0]) + steps
        return side.iloc[target] if 0 <= target < len(side) else None

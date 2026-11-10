"""Transaction costs, using the rates in force on the trade date."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class CostRates(BaseModel):
    effective_from: date
    brokerage_per_order: float
    stt_sell_pct: float
    stt_exercise_pct: float
    exchange_pct: float
    sebi_pct: float
    stamp_buy_pct: float
    gst_pct: float


class CostSchedule(BaseModel):
    schedules: list[CostRates] = Field(min_length=1)


class CostModel:
    def __init__(self, schedule: CostSchedule):
        self._rates = sorted(schedule.schedules, key=lambda r: r.effective_from)

    def rates(self, on: date) -> CostRates:
        eligible = [r for r in self._rates if r.effective_from <= on]
        if not eligible:
            raise ValueError(f"No cost schedule covers {on}")
        return eligible[-1]

    def order(self, is_buy: bool, price: float, units: float, on: date) -> float:
        """Total charges for one executed option order."""
        rates = self.rates(on)
        premium = price * units
        brokerage = rates.brokerage_per_order
        exchange = premium * rates.exchange_pct / 100
        sebi = premium * rates.sebi_pct / 100
        gst = (brokerage + exchange + sebi) * rates.gst_pct / 100
        stt = 0.0 if is_buy else premium * rates.stt_sell_pct / 100
        stamp = premium * rates.stamp_buy_pct / 100 if is_buy else 0.0
        return brokerage + exchange + sebi + gst + stt + stamp

    def exercise(self, intrinsic: float, units: float, on: date) -> float:
        """STT on an option held to expiry in the money, charged on intrinsic value."""
        return intrinsic * units * self.rates(on).stt_exercise_pct / 100


def load_cost_model(config_dir: Path) -> CostModel:
    with (config_dir / "costs.yaml").open() as f:
        return CostModel(CostSchedule.model_validate(yaml.safe_load(f)))

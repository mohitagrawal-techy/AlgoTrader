"""Risk configuration."""

from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class StressConfig(BaseModel):
    spot_moves_pct: list[float]
    iv_shifts_pct: list[float]


class MarginConfig(BaseModel):
    short_option_pct_of_notional: float
    hedged_max_loss_multiple: float
    hedged_min_pct_of_notional: float
    calendar_pct_of_notional: float


# How an exit amount is measured: a fraction of the premium, rupees per lot, or points of the
# option price (net premium) per lot.
ExitUnit = Literal["premium", "rupees", "points"]


class ExitRule(BaseModel):
    # Amounts in the unit beside each one. The names keep their old meaning for "premium".
    target_frac: float = Field(gt=0)
    stop_frac: float = Field(gt=0)
    exit_dte: int = Field(ge=0)
    target_unit: ExitUnit = "premium"
    stop_unit: ExitUnit = "premium"
    # Trailing stop: once in profit, the stop follows the best P&L at this distance.
    trail: float | None = Field(default=None, gt=0)
    trail_unit: ExitUnit = "premium"
    # Move-to-cost: once the gain reaches this, the stop moves up to the entry price.
    breakeven_at: float | None = Field(default=None, gt=0)
    breakeven_unit: ExitUnit = "premium"
    # Intraday strategies close every position by this time each day. None holds overnight.
    square_off: time | None = None


class PaperConfig(BaseModel):
    auto_trade: bool
    min_score: float
    max_new_per_cycle: int = Field(ge=0)
    idea_ttl_minutes: int = Field(gt=0)
    slippage_per_leg: float = Field(ge=0)
    max_entry_drift_pct: float = Field(gt=0)
    disabled_profiles: list[str] = Field(default_factory=list)


class RiskConfig(BaseModel):
    capital: float = Field(gt=0)
    max_loss_per_trade_pct: float = Field(gt=0)
    undefined_risk_loss_per_trade_pct: float = Field(gt=0)
    max_daily_loss_pct: float = Field(gt=0)
    max_open_risk_pct: float = Field(gt=0)
    max_underlying_risk_pct: float = Field(gt=0)
    max_margin_use_pct: float = Field(gt=0)
    max_stress_loss_pct: float = Field(gt=0)
    max_vega_pct: float = Field(gt=0)
    max_gamma_1pct_pct: float = Field(gt=0)
    max_delta_notional_multiple: float = Field(gt=0)
    max_open_positions: int = Field(gt=0)
    max_lots_per_trade: int = Field(gt=0)
    # Size each trade by money rather than by risk: as many lots as the margin room allows,
    # with no loss-based, stress or Greek caps. Set for strategies tested on their own budget.
    size_by_budget: bool = False
    # Structures with no bounded loss, such as a naked short straddle or strangle. Off unless
    # risk.yaml switches them on.
    allow_unlimited_risk: bool = False
    # Units one exchange order may hold, per underlying. Bigger orders are split into slices,
    # each a separate order with its own brokerage. An underlying not listed is never split.
    freeze_quantity: dict[str, int] = Field(default_factory=dict)
    # The most slices one leg may be split into; lots are capped to fit.
    max_order_slices: int = Field(default=10, ge=1, le=100)
    stop_gap_buffer: float = Field(ge=1)
    stress: StressConfig
    margin: MarginConfig
    exits: dict[str, ExitRule]
    paper: PaperConfig

    def amount(self, pct: float) -> float:
        return self.capital * pct / 100

    def order_slices(self, symbol: str, units: int) -> int:
        """Orders needed to trade `units` of one contract, within the freeze quantity."""
        limit = self.freeze_quantity.get(symbol)
        if not limit or units <= 0:
            return 1
        return -(-int(units) // int(limit))


def load_risk_config(config_dir: Path) -> RiskConfig:
    with (config_dir / "risk.yaml").open() as f:
        return RiskConfig.model_validate(yaml.safe_load(f))

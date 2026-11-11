"""Risk configuration."""

from __future__ import annotations

from datetime import time
from pathlib import Path

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


class ExitRule(BaseModel):
    target_frac: float = Field(gt=0)
    stop_frac: float = Field(gt=0)
    exit_dte: int = Field(ge=0)
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
    stop_gap_buffer: float = Field(ge=1)
    stress: StressConfig
    margin: MarginConfig
    exits: dict[str, ExitRule]
    paper: PaperConfig

    def amount(self, pct: float) -> float:
        return self.capital * pct / 100


def load_risk_config(config_dir: Path) -> RiskConfig:
    with (config_dir / "risk.yaml").open() as f:
        return RiskConfig.model_validate(yaml.safe_load(f))

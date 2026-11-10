"""The bar a forward run must clear. Passing never enables live orders."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ShadowConfig(BaseModel):
    max_new_per_cycle: int = Field(ge=0)
    max_new_per_day: int = Field(ge=0)


class GatesConfig(BaseModel):
    min_weeks: float = Field(ge=0)
    min_trades: int = Field(ge=0)
    min_volatility_regimes: int = Field(ge=0)
    min_expiries: int = Field(ge=0)
    max_slippage_ratio: float = Field(gt=0)
    max_drawdown_pct: float = Field(gt=0)
    confidence_sigmas: float = Field(gt=0)
    min_drift_samples: int = Field(ge=1)
    shadow: ShadowConfig


def load_gates(config_dir: Path) -> GatesConfig:
    with (config_dir / "gates.yaml").open() as f:
        return GatesConfig.model_validate(yaml.safe_load(f))


@dataclass(frozen=True)
class GateInputs:
    backtest_verdict: str | None
    holdout_verdict: str | None
    reports_match_config: bool
    weeks: float
    trades: int
    volatility_regimes: int
    expiries: int
    expectancy: float | None
    expectancy_status: str
    drawdown_pct: float
    slippage_ratio: float | None


def evaluate_gates(inputs: GateInputs, config: GatesConfig) -> list[dict]:
    """Return every gate with its requirement, actual value and result."""

    def gate(key: str, label: str, passed: bool, required: str, actual: str) -> dict:
        return {
            "key": key,
            "label": label,
            "passed": bool(passed),
            "required": required,
            "actual": actual,
        }

    expectancy = inputs.expectancy
    ratio = inputs.slippage_ratio
    return [
        gate(
            "walk_forward",
            "Backtest passed walk-forward",
            inputs.backtest_verdict == "passed_walk_forward",
            "Passed after costs",
            inputs.backtest_verdict or "No report attached",
        ),
        gate(
            "holdout",
            "Backtest passed the holdout",
            inputs.holdout_verdict == "passed_holdout",
            "Passed after costs",
            inputs.holdout_verdict or "No report attached",
        ),
        gate(
            "same_config",
            "Backtest used this exact configuration",
            inputs.reports_match_config,
            "Matching configuration",
            "Matches" if inputs.reports_match_config else "Does not match",
        ),
        gate(
            "weeks",
            "Forward period",
            inputs.weeks >= config.min_weeks,
            f"At least {config.min_weeks:g} weeks",
            f"{inputs.weeks:.1f} weeks",
        ),
        gate(
            "trades",
            "Closed trades",
            inputs.trades >= config.min_trades,
            f"At least {config.min_trades}",
            str(inputs.trades),
        ),
        gate(
            "regimes",
            "Volatility regimes covered",
            inputs.volatility_regimes >= config.min_volatility_regimes,
            f"At least {config.min_volatility_regimes}",
            str(inputs.volatility_regimes),
        ),
        gate(
            "expiries",
            "Expiries covered",
            inputs.expiries >= config.min_expiries,
            f"At least {config.min_expiries}",
            str(inputs.expiries),
        ),
        gate(
            "expectancy_positive",
            "Expectancy after costs",
            expectancy is not None and expectancy > 0,
            "Above zero",
            "No trades"
            if expectancy is None
            else f"{'-' if expectancy < 0 else ''}₹{abs(expectancy):,.0f} per trade",
        ),
        gate(
            "expectancy_in_range",
            "Expectancy inside the backtest's range",
            inputs.expectancy_status == "in",
            "Inside the expected range",
            inputs.expectancy_status.replace("_", " "),
        ),
        gate(
            "drawdown",
            "Drawdown",
            inputs.drawdown_pct < config.max_drawdown_pct,
            f"Below {config.max_drawdown_pct:g}% of capital",
            f"{inputs.drawdown_pct:.1f}%",
        ),
        gate(
            "slippage",
            "Observed slippage",
            ratio is not None and ratio <= config.max_slippage_ratio,
            f"At most {config.max_slippage_ratio:g}x modelled",
            "No fills yet" if ratio is None else f"{ratio:.2f}x modelled",
        ),
    ]

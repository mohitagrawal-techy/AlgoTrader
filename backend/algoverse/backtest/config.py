"""Backtest configuration."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

HASHED_FILES = ("scoring.yaml", "risk.yaml", "costs.yaml", "backtest.yaml")


class SpreadConfig(BaseModel):
    base_pct: float = Field(ge=0)
    moneyness_pct_per_pct: float = Field(ge=0)
    illiquidity_pct: float = Field(ge=0)
    tick: float = Field(gt=0)
    depth_share: float = Field(gt=0)


class WalkForwardConfig(BaseModel):
    train_sessions: int = Field(gt=0)
    test_sessions: int = Field(gt=0)
    holdout_sessions: int = Field(ge=0)
    min_train_trades: int = Field(ge=0)
    workers: int = Field(ge=1)


class GridConfig(BaseModel):
    min_score: list[float] = Field(min_length=1)
    disabled_profiles: list[list[str]] = Field(min_length=1)


class StressWindow(BaseModel):
    name: str
    start: date
    end: date


class LabConfig(BaseModel):
    test_share: float = Field(default=0.3, gt=0, lt=1)
    min_sessions: int = Field(default=60, ge=2)
    min_test_trades: int = Field(default=15, ge=0)
    rounds: int = Field(default=3, ge=1, le=10)
    per_round: int = Field(default=4, ge=1, le=8)
    workers: int = Field(default=6, ge=1)


class BacktestConfig(BaseModel):
    symbols: list[str]
    expiries_per_underlying: int = Field(ge=1)
    strike_range: float = Field(gt=0)
    idea_ttl_minutes: int = Field(gt=0)
    max_entry_drift_pct: float = Field(gt=0)
    warmup_sessions: int = Field(ge=0)
    spread: SpreadConfig
    walk_forward: WalkForwardConfig
    grid: GridConfig
    monte_carlo_runs: int = Field(gt=0)
    tail_share: float = Field(gt=0, lt=1)
    min_trades_for_confidence: int = Field(gt=0)
    stress_windows: list[StressWindow] = Field(default_factory=list)
    lab: LabConfig = Field(default_factory=LabConfig)


def load_backtest_config(config_dir: Path) -> BacktestConfig:
    with (config_dir / "backtest.yaml").open() as f:
        return BacktestConfig.model_validate(yaml.safe_load(f))


def config_hash(config_dir: Path) -> str:
    digest = hashlib.sha256()
    for name in HASHED_FILES:
        digest.update((config_dir / name).read_bytes())
    return digest.hexdigest()[:16]

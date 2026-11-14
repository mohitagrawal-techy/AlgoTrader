"""Backtest configuration."""

from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

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
    # 0 picks a count from the machine's cores and free memory.
    workers: int = Field(ge=0)

    @field_validator("workers")
    @classmethod
    def _auto(cls, value: int) -> int:
        return auto_workers() if value == 0 else value


class GridConfig(BaseModel):
    min_score: list[float] = Field(min_length=1)
    disabled_profiles: list[list[str]] = Field(min_length=1)


class StressWindow(BaseModel):
    name: str
    start: date
    end: date


# One replay worker holds its own copy of the history: about 0.8 GB with NIFTY minute data.
WORKER_MEMORY_MB = 800
RESERVED_MEMORY_MB = 1024


def _available_memory_mb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES") / 2**20
    except (ValueError, OSError, AttributeError):
        return None


def auto_workers() -> int:
    """Replays to run at once: one per core, leaving one for the app, within free memory."""
    try:
        cores = len(os.sched_getaffinity(0))
    except AttributeError:
        cores = os.cpu_count() or 1
    count = max(1, cores - 1)
    memory = _available_memory_mb()
    if memory is not None:
        count = min(count, max(1, int((memory - RESERVED_MEMORY_MB) // WORKER_MEMORY_MB)))
    return count


class LabConfig(BaseModel):
    test_share: float = Field(default=0.3, gt=0, lt=1)
    min_sessions: int = Field(default=60, ge=2)
    min_test_trades: int = Field(default=15, ge=0)
    rounds: int = Field(default=3, ge=1, le=10)
    per_round: int = Field(default=4, ge=1, le=8)
    # 0 picks a count from the machine's cores and free memory.
    workers: int = Field(default=0, ge=0)

    @field_validator("workers")
    @classmethod
    def _auto(cls, value: int) -> int:
        return auto_workers() if value == 0 else value

    # Research stages. The training and test sessions are cut into this many periods of
    # equal length, and a strategy must be profitable in this share of those with trades.
    stage_folds: int = Field(default=6, ge=2, le=24)
    stage_min_positive_share: float = Field(default=0.6, gt=0, le=1)
    # Largest drawdown on unseen data, as a percent of the strategy's budget.
    stage_max_drawdown_pct: float = Field(default=15.0, gt=0)

    # The ladder splits the training period, using this last share for unseen checks.
    validate_share: float = Field(default=0.35, gt=0, lt=1)
    ladder_rungs: int = Field(default=4, ge=1, le=8)
    ladder_beam: int = Field(default=2, ge=1, le=5)
    ladder_steps: int = Field(default=10, ge=1, le=30)
    ladder_min_trades: int = Field(default=8, ge=0)
    # Chance of accepting a useless change, spread across every change tried on a rung.
    ladder_alpha: float = Field(default=0.2, gt=0, lt=1)


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

"""Scoring and filter configuration, and the event calendar."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from algoverse.strategies.base import GenerationConfig


class Weight(BaseModel):
    w: float = Field(gt=0)
    prefer: Literal["high", "low", "mid"] = "high"


class FilterConfig(BaseModel):
    max_spread_pct: float
    max_spread_abs: float
    min_oi: float
    min_volume: float
    max_dte: int
    max_cost_ratio: float
    min_coverage: float
    block_on_high_events: list[str]


class ScoringConfig(BaseModel):
    risk_free_rate: float
    forecast_iv_weight: float = Field(ge=0, le=1)
    generation: GenerationConfig
    filters: FilterConfig
    profiles: dict[str, dict[str, Weight]]


class Event(BaseModel):
    date: date
    name: str
    severity: Literal["high", "medium"]
    symbols: list[str] = Field(default_factory=list)


class EventCalendar(BaseModel):
    reviewed_through: date | None = None
    events: list[Event] = Field(default_factory=list)

    def is_reviewed(self, through: date) -> bool:
        return self.reviewed_through is not None and self.reviewed_through >= through

    def in_window(self, symbol: str, start: date, end: date) -> list[Event]:
        return [
            event
            for event in self.events
            if start <= event.date <= end and (not event.symbols or symbol in event.symbols)
        ]


def _load(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load_scoring_config(config_dir: Path) -> ScoringConfig:
    return ScoringConfig.model_validate(_load(config_dir / "scoring.yaml"))


def load_event_calendar(config_dir: Path) -> EventCalendar:
    return EventCalendar.model_validate(_load(config_dir / "events.yaml"))

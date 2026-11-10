"""Typed AI inputs and outputs, and configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

UNAVAILABLE = "unavailable"
OK = "ok"
OVER_LIMIT = "over_limit"


class AiConfig(BaseModel):
    enabled: bool
    provider: Literal["ollama"]
    model: str
    think: Literal["low", "medium", "high"] | None = None
    context_tokens: int = Field(default=8192, gt=0)
    base_url: str
    request_timeout_seconds: float = Field(gt=0)
    critique_top_n: int = Field(ge=0)
    max_score_penalty: int = Field(ge=0)
    critique_ttl_minutes: int = Field(ge=0)
    research_ttl_minutes: int = Field(ge=0)
    max_calls_per_day: int = Field(ge=0)
    cycle_time_limit_seconds: float = Field(gt=0)
    news_max_age_hours: int = Field(gt=0)
    news_max_headlines: int = Field(gt=0)
    news_feeds: list[str]


def load_ai_config(config_dir: Path) -> AiConfig:
    with (config_dir / "ai.yaml").open() as f:
        config = AiConfig.model_validate(yaml.safe_load(f))
    # A container reaches the host's Ollama at a different address. The model is unchanged.
    base_url = os.environ.get("ALGOVERSE_OLLAMA_BASE_URL")
    return config.model_copy(update={"base_url": base_url}) if base_url else config


class BriefEvent(BaseModel):
    name: str
    date: str | None = None
    impact: Literal["high", "medium", "low"]


class ResearchOutput(BaseModel):
    summary: str
    sentiment: Literal["bullish", "bearish", "neutral"]
    catalysts: list[str]
    risks: list[str]
    events: list[BriefEvent]
    source_ids: list[int]


class CritiqueOutput(BaseModel):
    thesis: str
    bear_case: str
    score_adjustment: int
    veto: bool
    veto_reason: str | None = None


@dataclass(frozen=True)
class Headline:
    title: str
    link: str
    published: str | None
    summary: str


@dataclass(frozen=True)
class AiReview:
    status: str
    model: str
    thesis: str = ""
    bear_case: str = ""
    adjustment: int = 0
    veto: bool = False
    veto_reason: str | None = None
    cached: bool = False

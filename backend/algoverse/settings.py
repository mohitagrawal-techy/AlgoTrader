"""Environment settings and YAML configuration."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ALGOVERSE_", env_file=REPO_ROOT / ".env", extra="ignore"
    )

    upstox_api_key: str = ""
    upstox_api_secret: SecretStr = SecretStr("")
    upstox_redirect_uri: str = "http://127.0.0.1:8000/auth/callback"
    data_dir: Path = REPO_ROOT / "data"
    config_dir: Path = REPO_ROOT / "config"
    ai_enabled: bool = True
    host: str = "127.0.0.1"


class Universe(BaseModel):
    underlyings: list[str]
    reference_indices: dict[str, str] = Field(default_factory=dict)
    expiries_per_underlying: int = Field(default=3, ge=1)
    snapshot_interval_minutes: int = Field(default=5, ge=1)


class CalendarConfig(BaseModel):
    holidays: set[date] = Field(default_factory=set)


def _load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load_universe(config_dir: Path) -> Universe:
    return Universe.model_validate(_load_yaml(config_dir / "universe.yaml"))


def load_calendar_config(config_dir: Path) -> CalendarConfig:
    return CalendarConfig.model_validate(_load_yaml(config_dir / "calendar.yaml"))


@lru_cache
def get_settings() -> Settings:
    return Settings()

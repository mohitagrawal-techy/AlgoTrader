"""Runs research and critique for one cycle."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from algoverse.ai.budget import AiGateway, AiLog
from algoverse.ai.client import LLMClient, build_client
from algoverse.ai.critique import apply_review, critique_idea
from algoverse.ai.event_store import AiEventStore
from algoverse.ai.news import fetch_headlines
from algoverse.ai.research import research_brief
from algoverse.ai.schemas import AiConfig, Headline
from algoverse.evaluation.context import MarketContext
from algoverse.evaluation.pipeline import Idea


class AiService:
    def __init__(
        self, config: AiConfig, client: LLMClient, ai_log: AiLog, fetch_news=None, event_store=None
    ):
        self.config = config
        self.event_store = event_store
        self.gateway = AiGateway(client, ai_log, config)
        self._fetch_news = fetch_news or (lambda now: fetch_headlines(config, now))

    @property
    def model(self) -> str:
        return self.gateway.model

    def _needs_news(self, symbols: list[str], now: datetime) -> bool:
        ttl = self.config.research_ttl_minutes
        return any(self.gateway.cached("research", s, now, ttl) is None for s in symbols)

    def briefs(self, symbols: list[str], now: datetime) -> dict[str, dict | None]:
        headlines: list[Headline] = self._fetch_news(now) if self._needs_news(symbols, now) else []
        ttl = self.config.research_ttl_minutes
        briefs = {s: research_brief(self.gateway, s, headlines, now, ttl) for s in symbols}
        if self.event_store is not None:
            for symbol, brief in briefs.items():
                if brief is not None:
                    self.event_store.add(symbol, brief["events"], now.date())
        return briefs

    def review(
        self,
        ideas: list[Idea],
        contexts: dict[str, MarketContext],
        briefs: dict[str, dict | None],
        now: datetime,
    ) -> list[Idea]:
        """Review the highest-ranked tradable ideas. Input order must be best first."""
        remaining = self.config.critique_top_n
        reviewed = []
        for idea in ideas:
            if idea.tradable and remaining > 0:
                remaining -= 1
                symbol = idea.candidate.symbol
                review = critique_idea(
                    self.gateway, idea, contexts[symbol], briefs.get(symbol), self.config, now
                )
                idea = apply_review(idea, review)
            reviewed.append(idea)
        return reviewed


def build_ai_service(config: AiConfig, data_dir: Path) -> AiService:
    return AiService(
        config,
        build_client(config),
        AiLog(Path(data_dir) / "ai.sqlite"),
        event_store=AiEventStore(data_dir),
    )

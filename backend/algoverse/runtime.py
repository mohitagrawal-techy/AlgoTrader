"""Wires configuration, storage and services together for the CLI and the web API."""

from __future__ import annotations

import logging
from datetime import date, datetime
from functools import cached_property
from zoneinfo import ZoneInfo

import pandas as pd

from algoverse.ai.event_store import load_live_calendar
from algoverse.ai.schemas import load_ai_config
from algoverse.ai.service import build_ai_service
from algoverse.backtest.jobs import BacktestJobs
from algoverse.brokers.auth import token_store
from algoverse.brokers.upstox import UpstoxMarketData
from algoverse.data.calendar import BrokerCalendar
from algoverse.data.candles import migrate_flat_layout, resolve_name
from algoverse.data.instruments import download_master, parse_option_contracts
from algoverse.data.options import compact_chains, migrate_old_layout
from algoverse.data.store import SnapshotStore
from algoverse.evaluation.config import EventCalendar, load_scoring_config
from algoverse.execution.cycle import CycleDeps
from algoverse.execution.ledger import Ledger
from algoverse.execution.live_candles import LiveCandles
from algoverse.execution.paper import PaperBroker
from algoverse.forward.runs import ForwardManager
from algoverse.lab.service import LabService
from algoverse.market.assistant import AssistantService
from algoverse.market.overview import OverviewService, load_market_config
from algoverse.research.service import ResearchService
from algoverse.risk.config import load_risk_config
from algoverse.risk.costs import load_cost_model
from algoverse.settings import Settings, get_settings, load_calendar_config, load_universe

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")


def organise_storage(settings: Settings | None = None) -> None:
    """Move data saved in older layouts into the organised one, and compact finished option
    chain days. Runs at start-up; safe to repeat."""
    settings = settings or get_settings()
    moved = migrate_old_layout(settings.data_dir)
    if moved:
        log.info("Moved %s option files into options/", moved)
    compact_chains(settings.data_dir, datetime.now(IST))
    universe = load_universe(settings.config_dir)
    store = SnapshotStore(settings.data_dir, universe.reference_indices)
    result = migrate_flat_layout(settings.data_dir, lambda name: resolve_name(name, store.aliases))
    if result.moved:
        log.info("Moved %s candle files into the organised layout", result.moved)


class Runtime:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        config_dir = self.settings.config_dir
        self.universe = load_universe(config_dir)
        self.store = SnapshotStore(self.settings.data_dir, self.universe.reference_indices)
        self.calendar = BrokerCalendar(
            self.settings.data_dir, load_calendar_config(config_dir).holidays
        )
        self.tokens = token_store(self.settings)
        self.market = UpstoxMarketData(self.tokens)
        risk = load_risk_config(config_dir)
        costs = load_cost_model(config_dir)
        self.ledger = Ledger(self.settings.data_dir / "paper.sqlite")
        ai_config = load_ai_config(config_dir)
        ai_on = self.settings.ai_enabled and ai_config.enabled
        self.deps = CycleDeps(
            store=self.store,
            broker=PaperBroker(self.ledger, risk, costs),
            scoring=load_scoring_config(config_dir),
            risk=risk,
            costs=costs,
            events=self.live_events(datetime.now(IST).date()),
            ai=build_ai_service(ai_config, self.settings.data_dir) if ai_on else None,
        )
        # Rules on today's candle and on minute candles read these during live paper trading.
        self.candles = LiveCandles(self.market, self.store)
        self.deps.today_bars = self.candles.today_bar
        self.deps.intraday_bars = self.candles.minutes

    @cached_property
    def overview(self) -> OverviewService:
        return OverviewService(
            self.settings.data_dir, self.settings.config_dir, self.market, self.store, self.calendar
        )

    @cached_property
    def assistant(self) -> AssistantService:
        config = load_ai_config(self.settings.config_dir)
        chosen = load_market_config(self.settings.config_dir).get("assistant", {})
        return AssistantService(
            config.model_copy(update={"model": chosen.get("model", config.model)}),
            self.settings.ai_enabled and config.enabled,
            self.overview,
            self.settings.data_dir,
            think=chosen.get("think"),
        )

    @cached_property
    def backtest_jobs(self) -> BacktestJobs:
        return BacktestJobs(self.settings.data_dir, self.settings.config_dir)

    @cached_property
    def lab(self) -> LabService:
        return LabService(
            self.settings.data_dir, self.settings.config_dir, self.settings.ai_enabled
        )

    @cached_property
    def research(self) -> ResearchService:
        return ResearchService(self.settings.data_dir, self.settings.config_dir)

    @cached_property
    def forward(self) -> ForwardManager:
        return ForwardManager(self.settings, self.store, self.candles)

    def live_events(self, today: date) -> EventCalendar:
        return load_live_calendar(self.settings.config_dir, self.settings.data_dir, today)

    def contracts(self, now: datetime) -> pd.DataFrame:
        """Return today's contract master, downloading it if not yet stored."""
        if not self.store.has_contracts(now.date()):
            self.refresh_contracts(now)
        return self.store.read_contracts(now.date())

    def refresh_contracts(self, now: datetime) -> int:
        contracts = parse_option_contracts(download_master())
        self.store.write_contracts(now.date(), contracts)
        return len(contracts)

"""One research and paper-trading cycle over a set of fresh chains."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime

import pandas as pd

from algoverse.ai.service import AiService
from algoverse.analytics.chain import enrich_chain
from algoverse.dashboard import publish
from algoverse.data.store import SnapshotStore
from algoverse.evaluation.config import EventCalendar, ScoringConfig
from algoverse.evaluation.context import build_context, reference_atm_iv
from algoverse.evaluation.pipeline import Idea, generate_ideas, rank_key
from algoverse.execution.paper import PaperBroker, StepReport
from algoverse.risk.config import RiskConfig
from algoverse.risk.costs import CostModel
from algoverse.risk.limits import evaluate_risk

VIX_SYMBOL = "INDIAVIX"
log = logging.getLogger(__name__)


@dataclass
class CycleDeps:
    store: SnapshotStore
    broker: PaperBroker
    scoring: ScoringConfig
    risk: RiskConfig
    costs: CostModel
    events: EventCalendar
    ai: AiService | None = None
    publish: bool = True
    custom: object | None = None


@dataclass
class CycleResult:
    ideas: list[Idea] = field(default_factory=list)
    report: StepReport | None = None
    submitted: list[int] = field(default_factory=list)
    contexts: dict = field(default_factory=dict)


def _iv_series_name(symbol: str) -> str:
    return f"iv_{symbol}"


def _iv_history(store: SnapshotStore, symbol: str, now: datetime) -> pd.Series:
    frame = store.read_candles(_iv_series_name(symbol))
    if frame.empty:
        return pd.Series(dtype=float)
    return frame.loc[pd.to_datetime(frame["ts"]).dt.date < now.date(), "close"]


def store_reference_iv(
    store: SnapshotStore, symbol: str, chain: pd.DataFrame, now: datetime
) -> None:
    """Keep one reference ATM IV per day. The latest snapshot of the day replaces earlier ones."""
    iv = reference_atm_iv(chain)
    if iv is not None:
        frame = pd.DataFrame({"ts": [pd.Timestamp(now.date())], "close": [iv]})
        store.upsert_candles(_iv_series_name(symbol), frame)


def prepare_chains(
    raw: dict[str, pd.DataFrame], contracts: pd.DataFrame, now: datetime, r: float
) -> dict[str, pd.DataFrame]:
    """Add analytics and lot sizes. Contracts missing from the master are dropped."""
    lots = contracts[["instrument_key", "lot_size"]].drop_duplicates("instrument_key")
    return {
        symbol: enrich_chain(chain, now, r).merge(lots, on="instrument_key", how="inner")
        for symbol, chain in raw.items()
        if not chain.empty
    }


def run_cycle(
    deps: CycleDeps,
    raw_chains: dict[str, pd.DataFrame],
    contracts: pd.DataFrame,
    now: datetime,
    trade: bool = True,
) -> CycleResult:
    """Process fills and exits, rank ideas, then queue the best for the next snapshot."""
    chains = prepare_chains(raw_chains, contracts, now, deps.scoring.risk_free_rate)
    result = CycleResult()
    if trade:
        result.report = deps.broker.step(now, chains)
    portfolio = deps.broker.portfolio_state(now)
    vix = deps.store.read_candles(VIX_SYMBOL)
    markets = {}
    for symbol, chain in chains.items():
        if chain.empty:
            continue
        context = build_context(
            symbol,
            now,
            chain,
            deps.store.read_candles(symbol),
            vix,
            _iv_history(deps.store, symbol, now),
            deps.events,
            deps.scoring.risk_free_rate,
        )
        ideas = generate_ideas(
            context, chain, deps.scoring, deps.risk, deps.costs, portfolio, deps.custom
        )
        result.ideas += [replace(idea, ctx_spot=context.spot) for idea in ideas]
        markets[symbol] = (context, chain)
        result.contexts[symbol] = context
        if trade:
            store_reference_iv(deps.store, symbol, chain, now)
    result.ideas.sort(key=rank_key)
    briefs: dict[str, dict | None] = {}
    if deps.ai is not None and markets:
        try:
            result.ideas, briefs = _apply_ai(deps.ai, result.ideas, markets, now)
        except Exception:  # An AI failure must never interrupt paper trading.
            log.exception("AI review failed. Continuing with numeric scores only.")
    if trade and deps.risk.paper.auto_trade:
        result.submitted = _submit_best(deps, result.ideas, now)
    if markets and deps.publish:
        try:
            publish(
                deps.store.data_dir,
                now,
                result.ideas,
                markets,
                deps.scoring.risk_free_rate,
                briefs,
            )
        except Exception:  # A display failure must never interrupt paper trading.
            log.exception("Could not publish dashboard data")
    return result


def _apply_ai(
    ai: AiService, ideas: list[Idea], markets: dict, now: datetime
) -> tuple[list[Idea], dict[str, dict | None]]:
    ai.gateway.begin_cycle()
    contexts = {symbol: pair[0] for symbol, pair in markets.items()}
    briefs = ai.briefs(list(markets), now)
    return sorted(ai.review(ideas, contexts, briefs, now), key=rank_key), briefs


def _submit_best(deps: CycleDeps, ideas: list[Idea], now: datetime) -> list[int]:
    paper = deps.risk.paper
    submitted: list[int] = []
    for idea in ideas:
        if len(submitted) >= paper.max_new_per_cycle:
            break
        if not idea.tradable or idea.final_score < paper.min_score:
            continue
        if idea.candidate.profile in paper.disabled_profiles:
            continue
        state = deps.broker.portfolio_state(now)
        decision = evaluate_risk(
            idea.candidate, idea.structure, idea.plan, idea.stress, state, deps.risk
        )
        if decision.approved:
            submitted.append(deps.broker.submit(replace(idea, risk=decision), now))
    return submitted

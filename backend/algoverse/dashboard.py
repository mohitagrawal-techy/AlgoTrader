"""Publishes the latest ideas and market analytics as JSON for the dashboard."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from algoverse.analytics.iv import term_structure
from algoverse.evaluation.context import MarketContext
from algoverse.evaluation.pipeline import Idea
from algoverse.evaluation.structure import horizon_pnl

PAYOFF_POINTS = 160
PAYOFF_SIGMAS = 3.0
CHAIN_RANGE = 0.06
MAX_ZONES = 6
IDEAS_FILE = "ideas.json"
MARKET_FILE = "market.json"


def clean(value):
    """Convert to strict JSON types. NaN and infinities become null."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [clean(v) for v in value]
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, float | np.floating):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _payoff(idea: Idea, r: float) -> list[list[float]]:
    structure = idea.structure
    forward = idea.candidate.legs[0].forward
    strikes = [leg.strike for leg in idea.candidate.legs]
    reach = PAYOFF_SIGMAS * structure.expected_move
    low = min(forward - reach, min(strikes) - 0.25 * reach)
    high = max(forward + reach, max(strikes) + 0.25 * reach)
    grid = np.unique(np.concatenate([np.linspace(low, high, PAYOFF_POINTS), strikes]))
    pnl = horizon_pnl(idea.candidate, grid, r)
    return [[float(s), float(p)] for s, p in zip(grid, pnl, strict=True)]


def idea_id(idea: Idea) -> str:
    candidate = idea.candidate
    legs = "_".join(
        f"{leg.side:+d}{leg.strike:g}{leg.option_type}{leg.expiry}" for leg in candidate.legs
    )
    return f"{candidate.symbol}:{candidate.strategy}:{legs}"


def serialize_idea(idea: Idea, r: float) -> dict:
    candidate, structure, plan = idea.candidate, idea.structure, idea.plan
    decision = idea.risk
    rejections = list(idea.rejections) or list(decision.reasons if decision else ())
    return {
        "id": idea_id(idea),
        "symbol": candidate.symbol,
        "strategy": candidate.strategy,
        "profile": candidate.profile,
        "bias": candidate.bias,
        "front_expiry": candidate.front_expiry,
        "tradable": idea.tradable,
        "score": idea.final_score,
        "base_score": idea.card.composite,
        "ai": asdict(idea.ai) if idea.ai else None,
        "coverage": idea.card.coverage,
        "rejections": rejections,
        "lots": decision.lots if decision and decision.approved else 0,
        "spot": idea.ctx_spot,
        "legs": [
            {
                "side": leg.side,
                "ratio": leg.ratio,
                "strike": leg.strike,
                "option_type": leg.option_type,
                "expiry": leg.expiry,
                "lot_size": leg.lot_size,
                "bid": leg.bid,
                "ask": leg.ask,
                "iv": leg.iv,
                "delta": leg.delta,
                "oi": leg.oi,
                "volume": leg.volume,
            }
            for leg in candidate.legs
        ],
        "structure": asdict(structure),
        "plan": asdict(plan),
        "stress": asdict(idea.stress),
        "parts": [
            {
                "name": part.result.name,
                "group": part.result.group,
                "raw": part.result.raw,
                "score": part.result.score,
                "adjusted": part.adjusted,
                "weight": part.weight,
                "prefer": part.prefer,
                "note": part.result.note,
            }
            for part in idea.card.parts
        ],
        "payoff": _payoff(idea, r),
    }


def _quote_side(row: pd.Series | None) -> dict | None:
    if row is None:
        return None
    return {
        "bid": row["bid"],
        "ask": row["ask"],
        "ltp": row["ltp"],
        "iv": row["iv"],
        "delta": row["delta"],
        "oi": row["oi"],
        "oi_change": row["oi"] - row["prev_oi"],
        "volume": row["volume"],
    }


def _chain_rows(group: pd.DataFrame, forward: float) -> list[dict]:
    near = group[(group["strike"] / forward - 1).abs() <= CHAIN_RANGE]
    rows = []
    for strike, by_strike in near.groupby("strike", sort=True):
        sides = {row["option_type"]: row for _, row in by_strike.iterrows()}
        rows.append(
            {
                "strike": strike,
                "ce": _quote_side(sides.get("CE")),
                "pe": _quote_side(sides.get("PE")),
            }
        )
    return rows


def serialize_market(
    context: MarketContext, chain: pd.DataFrame, research: dict | None = None
) -> dict:
    live = chain[chain["T"] > 0]
    regime = context.regime
    ranking = context.iv.ranking
    structure = term_structure(live)
    expiries = []
    for expiry, group in live.groupby("expiry", sort=True):
        stats = context.by_expiry[expiry]
        expiries.append(
            {
                "expiry": expiry,
                "dte": (expiry - context.now.date()).days,
                "forward": stats.forward,
                "atm_iv": stats.atm_iv,
                "expected_move": stats.expected_move,
                "skew": stats.skew,
                "pcr": stats.pcr,
                "max_pain": stats.max_pain,
                "buildup": stats.buildup,
                "walls": stats.walls,
                "zones": [
                    {
                        "center": zone.center,
                        "low": zone.low,
                        "high": zone.high,
                        "methods": zone.methods,
                        "strength": zone.strength,
                    }
                    for zone in stats.zones[:MAX_ZONES]
                ],
                "rows": _chain_rows(group, stats.forward),
            }
        )
    return {
        "symbol": context.symbol,
        "spot": context.spot,
        "iv_rank": None if ranking is None else ranking.rank,
        "iv_percentile": None if ranking is None else ranking.percentile,
        "iv_sample_size": None if ranking is None else ranking.sample_size,
        "iv_source": context.iv.source,
        "realised_vol": context.realised_vol,
        "vix_change_pct": context.vix_change_pct,
        "trend": None if regime is None else regime.trend,
        "adx": None if regime is None else regime.adx,
        "term_slope": context.term_slope,
        "term_structure": [
            {
                "expiry": row["expiry"],
                "dte": (row["expiry"] - context.now.date()).days,
                "atm_iv": row["atm_iv"],
            }
            for _, row in structure.iterrows()
        ],
        "expiries": expiries,
        "research": research,
    }


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean(payload), allow_nan=False))
    os.replace(tmp, path)


def publish(
    data_dir: Path,
    now: datetime,
    ideas: list[Idea],
    markets: dict[str, tuple[MarketContext, pd.DataFrame]],
    r: float,
    briefs: dict[str, dict | None] | None = None,
) -> None:
    briefs = briefs or {}
    folder = Path(data_dir) / "dashboard"
    _write(
        folder / IDEAS_FILE,
        {"generated_at": now, "ideas": [serialize_idea(idea, r) for idea in ideas]},
    )
    _write(
        folder / MARKET_FILE,
        {
            "generated_at": now,
            "symbols": {
                symbol: serialize_market(context, chain, briefs.get(symbol))
                for symbol, (context, chain) in markets.items()
            },
        },
    )


def read_published(data_dir: Path, name: str) -> dict | None:
    try:
        return json.loads((Path(data_dir) / "dashboard" / name).read_text())
    except FileNotFoundError:
        return None

"""Exit planning, position sizing and portfolio limits."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from algoverse.evaluation.structure import StructureMetrics
from algoverse.risk.config import RiskConfig
from algoverse.risk.stress import StressResult
from algoverse.strategies.base import Candidate


@dataclass(frozen=True)
class ExitPlan:
    """Rupee levels for one lot. `planned_loss` includes the stop gap buffer."""

    target_pnl: float
    stop_pnl: float
    exit_dte: int
    planned_loss: float
    # Trailing distance and move-to-cost trigger, in rupees per lot. None when unused.
    trail: float | None = None
    breakeven_at: float | None = None


@dataclass
class PortfolioState:
    capital: float
    day_pnl: float = 0.0
    halted: bool = False
    open_risk: float = 0.0
    margin_used: float = 0.0
    position_count: int = 0
    delta_notional: float = 0.0
    vega: float = 0.0
    gamma_1pct: float = 0.0
    risk_by_symbol: dict[str, float] = field(default_factory=dict)
    open_keys: set[tuple] = field(default_factory=set)


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    lots: int
    planned_loss: float
    margin: float
    reasons: tuple[str, ...]


def position_key(candidate: Candidate) -> tuple:
    return (candidate.symbol, candidate.strategy, candidate.front_expiry.isoformat())


def make_exit_plan(
    candidate: Candidate, structure: StructureMetrics, config: RiskConfig
) -> ExitPlan:
    rule = config.exits[candidate.profile]
    basis = abs(structure.entry_debit)
    lot = candidate.legs[0].lot_size if candidate.legs else 1

    def rupees(value: float | None, unit: str) -> float | None:
        if value is None:
            return None
        if unit == "rupees":
            return value
        if unit == "points":
            return value * lot
        return value * basis

    target = rupees(rule.target_frac, getattr(rule, "target_unit", "premium"))
    stop = rupees(rule.stop_frac, getattr(rule, "stop_unit", "premium"))
    planned = stop * config.stop_gap_buffer
    if structure.bounded:
        target = min(target, max(structure.max_profit, 0.0))
        stop = min(stop, structure.max_loss)
        planned = min(planned, structure.max_loss)
    return ExitPlan(
        target,
        -stop,
        rule.exit_dte,
        planned,
        rupees(getattr(rule, "trail", None), getattr(rule, "trail_unit", "premium")),
        rupees(getattr(rule, "breakeven_at", None), getattr(rule, "breakeven_unit", "premium")),
    )


def _reject(reason: str, plan: ExitPlan, structure: StructureMetrics) -> RiskDecision:
    return RiskDecision(False, 0, plan.planned_loss, structure.margin, (reason,))


def _size_by_budget(
    structure: StructureMetrics, plan: ExitPlan, state: PortfolioState, config: RiskConfig
) -> RiskDecision:
    """As many lots as the money left allows: the budget's share for trading, less what open
    positions already tie up, over one lot's premium or margin."""
    if structure.margin <= 0:
        return _reject("structure ties up no money to size from", plan, structure)
    left = config.amount(config.max_margin_use_pct) - state.margin_used
    lots = min(math.floor(left / structure.margin), config.max_lots_per_trade)
    if lots <= 0:
        return _reject(
            f"budget: one lot needs {structure.margin:,.0f} and {max(left, 0):,.0f} is free",
            plan,
            structure,
        )
    return RiskDecision(True, lots, plan.planned_loss * lots, structure.margin * lots, ())


def evaluate_risk(
    candidate: Candidate,
    structure: StructureMetrics,
    plan: ExitPlan,
    stress: StressResult,
    state: PortfolioState,
    config: RiskConfig,
) -> RiskDecision:
    """Return the largest lot count that satisfies every limit, or a rejection with the cause."""
    if state.halted:
        return _reject("daily loss limit reached", plan, structure)
    if position_key(candidate) in state.open_keys:
        return _reject("same structure is already open", plan, structure)
    if state.position_count >= config.max_open_positions:
        return _reject("maximum open positions reached", plan, structure)
    if plan.planned_loss <= 0:
        return _reject("structure has no measurable planned loss", plan, structure)

    if config.size_by_budget:
        return _size_by_budget(structure, plan, state, config)

    per_trade_pct = (
        config.max_loss_per_trade_pct
        if structure.bounded
        else config.undefined_risk_loss_per_trade_pct
    )
    symbol_risk = state.risk_by_symbol.get(candidate.symbol, 0.0)
    worst_loss = max(-stress.worst_pnl, 0.0)
    rooms: dict[str, float] = {
        "per-trade loss limit": config.amount(per_trade_pct) / plan.planned_loss,
        "open risk limit": (config.amount(config.max_open_risk_pct) - state.open_risk)
        / plan.planned_loss,
        "underlying risk limit": (config.amount(config.max_underlying_risk_pct) - symbol_risk)
        / plan.planned_loss,
        "maximum lots": float(config.max_lots_per_trade),
    }
    if structure.margin > 0:
        rooms["margin limit"] = (
            config.amount(config.max_margin_use_pct) - state.margin_used
        ) / structure.margin
    if worst_loss > 0:
        rooms[
            f"stress loss limit ({stress.spot_move_pct:+g}% spot, {stress.iv_shift_pct:+g}% IV)"
        ] = config.amount(config.max_stress_loss_pct) / worst_loss

    binding = min(rooms, key=rooms.get)
    lots = max(math.floor(rooms[binding]), 0)
    if lots == 0:
        needed = rooms[binding]
        return _reject(
            f"{binding}: one lot uses {1 / needed:.1f}x the room"
            if needed > 0
            else f"{binding}: no room left",
            plan,
            structure,
        )

    greek_limits = (
        (
            "delta",
            state.delta_notional,
            structure.delta_notional,
            config.capital * config.max_delta_notional_multiple,
        ),
        ("vega", state.vega, structure.net_vega, config.amount(config.max_vega_pct)),
    )

    def fits(count: int) -> str | None:
        for name, current, per_lot, limit in greek_limits:
            if abs(current + per_lot * count) > limit:
                return name
        gamma_limit = config.amount(config.max_gamma_1pct_pct)
        return "gamma" if state.gamma_1pct + structure.gamma_1pct * count > gamma_limit else None

    breach = fits(lots)
    while lots > 0 and breach is not None:
        last_breach = breach
        lots -= 1
        breach = fits(lots) if lots else last_breach
    if lots == 0:
        return _reject(f"net {breach} limit", plan, structure)
    return RiskDecision(True, lots, plan.planned_loss * lots, structure.margin * lots, ())

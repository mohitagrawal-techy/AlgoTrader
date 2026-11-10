"""Payoff and risk metrics for one lot of a candidate, entered at the bid/ask."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
from scipy.stats import norm

from algoverse.analytics.pricing import black76_price
from algoverse.evaluation.context import MarketContext
from algoverse.risk.config import RiskConfig
from algoverse.risk.costs import CostModel
from algoverse.risk.sizing import estimate_margin
from algoverse.strategies.base import Candidate

GRID_POINTS = 4001
Z_POINTS = 2401
MIN_WIDE_RANGE = 0.30
REWARD_SIGMAS = 2.0


@dataclass(frozen=True)
class StructureMetrics:
    front_expiry: date
    dte: int
    entry_debit: float  # Positive when premium is paid, negative when received.
    net_mid: float
    max_loss: float
    max_profit: float
    bounded: bool
    breakevens: tuple[float, ...]
    reward: float
    reward_risk: float | None
    pop: float
    ev: float
    net_ev: float
    round_trip_cost: float
    exit_spread: float
    cost_ratio: float | None
    margin: float
    net_delta: float
    net_gamma: float
    net_theta: float
    net_vega: float
    delta_notional: float
    gamma_1pct: float
    theta_gamma: float | None
    breakeven_distance: float | None
    expected_move: float


def horizon_pnl(candidate: Candidate, spots, r: float) -> np.ndarray:
    """P&L of one lot at the front expiry. Later-dated legs are repriced at their own IV."""
    spots = np.asarray(spots, float)
    horizon = min(leg.T for leg in candidate.legs)
    total = np.zeros_like(spots)
    for leg in candidate.legs:
        remaining = max(leg.T - horizon, 0.0)
        value = black76_price(
            spots * np.exp(r * remaining), leg.strike, remaining, leg.iv, r, leg.is_call
        )
        total += leg.side * leg.units * (value - leg.entry_price)
    return total


def _breakevens(grid: np.ndarray, pnl: np.ndarray) -> tuple[float, ...]:
    sign_change = np.where(np.sign(pnl[:-1]) * np.sign(pnl[1:]) < 0)[0]
    return tuple(
        float(grid[i] - pnl[i] * (grid[i + 1] - grid[i]) / (pnl[i + 1] - pnl[i]))
        for i in sign_change
    )


def analyse(
    candidate: Candidate, ctx: MarketContext, risk: RiskConfig, costs: CostModel, forecast_w: float
) -> StructureMetrics:
    legs = candidate.legs
    front = min(legs, key=lambda leg: leg.T)
    stats = ctx.by_expiry[front.expiry]
    forward = front.forward
    horizon = front.T
    iv = stats.atm_iv if stats.atm_iv is not None else front.iv
    iv_std = iv * np.sqrt(horizon)

    wide = max(MIN_WIDE_RANGE, 6 * iv_std)
    strikes = [leg.strike for leg in legs]
    grid = np.unique(
        np.concatenate(
            [np.linspace(forward * (1 - wide), forward * (1 + wide), GRID_POINTS), strikes]
        )
    )
    grid = grid[grid > 0]
    pnl = horizon_pnl(candidate, grid, ctx.r)
    net_calls = sum(leg.side * leg.ratio for leg in legs if leg.is_call)
    net_puts = sum(leg.side * leg.ratio for leg in legs if not leg.is_call)
    bounded = net_calls >= 0 and net_puts >= 0
    max_loss = max(-float(pnl.min()), 0.0)
    max_profit = float(pnl.max())
    near = np.abs(grid / forward - 1) <= REWARD_SIGMAS * iv_std
    reward = float(pnl[near].max()) if near.any() else max_profit
    breakevens = _breakevens(grid, pnl)

    realised = ctx.realised_vol
    forecast = iv if realised is None else forecast_w * iv + (1 - forecast_w) * realised
    forecast_std = forecast * np.sqrt(horizon)
    z = np.linspace(-6, 6, Z_POINTS)
    weights = norm.pdf(z)
    weights /= weights.sum()
    outcomes = forward * np.exp(z * forecast_std - 0.5 * forecast_std**2)
    outcome_pnl = horizon_pnl(candidate, outcomes, ctx.r)
    pop = float(weights[outcome_pnl > 0].sum())
    ev = float(weights @ outcome_pnl)

    today = ctx.now.date()
    round_trip = sum(
        costs.order(leg.side > 0, leg.entry_price, leg.units, today)
        + costs.order(leg.side < 0, leg.exit_price_estimate, leg.units, today)
        for leg in legs
    )
    exit_spread = sum(leg.spread / 2 * leg.units for leg in legs)
    entry_debit = sum(leg.side * leg.entry_price * leg.units for leg in legs)
    net_mid = sum(leg.side * leg.mid * leg.units for leg in legs)

    net_delta = sum(leg.side * leg.delta * leg.units for leg in legs)
    net_gamma = sum(leg.side * leg.gamma * leg.units for leg in legs)
    net_theta = sum(leg.side * leg.theta * leg.units for leg in legs)
    net_vega = sum(leg.side * leg.vega * leg.units for leg in legs)
    gamma_1pct = 0.5 * abs(net_gamma) * (ctx.spot * 0.01) ** 2
    daily_move = ctx.spot * iv / np.sqrt(252)
    gamma_day = 0.5 * abs(net_gamma) * daily_move**2
    theta_gamma = None if gamma_day == 0 else float(abs(net_theta) / gamma_day)

    expected_move = forward * iv_std
    distance = (
        None
        if not breakevens or expected_move <= 0
        else float(min(abs(b - forward) for b in breakevens) / expected_move)
    )
    total_cost = round_trip + exit_spread
    return StructureMetrics(
        front_expiry=front.expiry,
        dte=(front.expiry - today).days,
        entry_debit=float(entry_debit),
        net_mid=float(net_mid),
        max_loss=max_loss,
        max_profit=max_profit,
        bounded=bounded,
        breakevens=breakevens,
        reward=reward,
        reward_risk=None if max_loss == 0 else reward / max_loss,
        pop=pop,
        ev=ev,
        net_ev=ev - total_cost,
        round_trip_cost=float(round_trip),
        exit_spread=float(exit_spread),
        cost_ratio=None if reward <= 0 else float(total_cost / reward),
        margin=estimate_margin(legs, ctx.spot, entry_debit, max_loss, bounded, risk.margin),
        net_delta=float(net_delta),
        net_gamma=float(net_gamma),
        net_theta=float(net_theta),
        net_vega=float(net_vega),
        delta_notional=float(net_delta * ctx.spot),
        gamma_1pct=float(gamma_1pct),
        theta_gamma=theta_gamma,
        breakeven_distance=distance,
        expected_move=float(expected_move),
    )

"""Portfolio views for the risk monitor: exposure per underlying and a stress grid."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np

from algoverse.analytics.chain import years_to_expiry
from algoverse.analytics.pricing import black76_price, implied_vol
from algoverse.execution.ledger import Position
from algoverse.risk.config import RiskConfig

# Below this many years to expiry the option is priced at intrinsic value.
MIN_T = 1e-6


def exposure_by_symbol(positions: list[Position], risk: RiskConfig) -> list[dict]:
    """Planned risk, Greeks and open P&L for each underlying, largest planned risk first."""
    rows: dict[str, dict] = {}
    for position in positions:
        row = rows.setdefault(
            position.symbol,
            {
                "symbol": position.symbol,
                "positions": 0,
                "planned_risk": 0.0,
                "limit": risk.amount(risk.max_underlying_risk_pct),
                "delta_notional": 0.0,
                "vega": 0.0,
                "gamma_1pct": 0.0,
                "open_pnl": 0.0,
            },
        )
        row["positions"] += 1
        row["planned_risk"] += position.planned_loss
        row["open_pnl"] += position.unrealized_pnl - position.entry_costs
        gamma = 0.0
        for leg in position.legs:
            if leg.closed:
                continue
            row["delta_notional"] += leg.side * leg.units * leg.delta * position.last_spot
            row["vega"] += leg.side * leg.units * leg.vega
            gamma += leg.side * leg.units * leg.gamma
        row["gamma_1pct"] += 0.5 * gamma * (position.last_spot * 0.01) ** 2
    return sorted(rows.values(), key=lambda row: -row["planned_risk"])


def _leg_pnl_grid(leg, spot: float, T: float, moves, shifts, r: float) -> tuple[np.ndarray, bool]:
    """P&L of one open leg across the grid, relative to its current mark.

    Reprices with Black-76 at the volatility implied by the mark, using spot as the forward.
    Falls back to delta and gamma alone when no volatility can be implied. The flag says so.
    """
    move = spot * moves / 100
    sign = leg.side * leg.units
    is_call = leg.option_type == "CE"
    iv = np.nan
    if leg.mark is not None and T > MIN_T:
        iv = float(implied_vol(leg.mark, spot, leg.strike, T, r, is_call))
    if np.isfinite(iv):
        value = black76_price(spot + move, leg.strike, T, iv * (1 + shifts / 100), r, is_call)
        return sign * (value - leg.mark), True
    approx = leg.delta * move + 0.5 * leg.gamma * move**2
    return sign * np.broadcast_to(approx, np.broadcast(move, shifts).shape), False


def portfolio_stress(positions: list[Position], risk: RiskConfig, r: float, now: datetime) -> dict:
    """Instant P&L of all open legs across the configured spot moves and IV shifts.

    Every underlying moves by the same percentage at once, which is the harsh case.
    """
    moves = np.asarray(risk.stress.spot_moves_pct, float)
    if 0.0 not in moves:
        moves = np.sort(np.append(moves, 0.0))
    shifts = np.asarray(risk.stress.iv_shifts_pct, float)
    move_grid, shift_grid = np.meshgrid(moves, shifts, indexing="ij")
    pnl = np.zeros_like(move_grid)
    approximated = 0
    for position in positions:
        for leg in position.legs:
            if leg.closed:
                continue
            T = max(years_to_expiry(date.fromisoformat(leg.expiry), now), 0.0)
            grid, repriced = _leg_pnl_grid(leg, position.last_spot, T, move_grid, shift_grid, r)
            pnl += grid
            approximated += not repriced
    return {
        "spot_moves_pct": moves.tolist(),
        "iv_shifts_pct": shifts.tolist(),
        "pnl": pnl.tolist(),
        "limit": risk.amount(risk.max_stress_loss_pct),
        "approximated_legs": approximated,
    }

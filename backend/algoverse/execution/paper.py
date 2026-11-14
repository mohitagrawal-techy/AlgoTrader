"""Paper execution on snapshot quotes.

Every decision fills on the next snapshot, never on the one that produced it. Fills cross the
spread and pay slippage. No part of this module can reach a broker.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from algoverse.execution.ledger import (
    CANCELLED,
    CLOSED,
    EXITING,
    LIVE_STATUSES,
    OPEN,
    PENDING,
    Ledger,
    PaperLeg,
    Position,
)
from algoverse.risk.config import RiskConfig
from algoverse.risk.costs import CostModel
from algoverse.risk.limits import PortfolioState

IST = ZoneInfo("Asia/Kolkata")
TIME_EXIT = time(15, 0)
SETTLEMENT_TIME = time(15, 30)
HALT_KEY = "halted_on"
QUOTE_COLUMNS = ["bid", "ask", "mid", "bid_qty", "ask_qty", "delta", "gamma", "vega"]


@dataclass
class StepReport:
    opened: list[int] = field(default_factory=list)
    closed: list[int] = field(default_factory=list)
    cancelled: list[int] = field(default_factory=list)
    exits_triggered: list[int] = field(default_factory=list)
    halted: bool = False


def effective_stop(position: Position) -> tuple[float, str]:
    """The stop level now and its exit reason, after any trailing or move-to-cost.

    Both only tighten the stop, and only once the position has been in profit."""
    stop, reason = position.stop_pnl, "stop"
    peak = position.peak_pnl
    if position.breakeven_pnl is not None and peak >= position.breakeven_pnl and stop < 0:
        stop, reason = 0.0, "stop_at_cost"
    if position.trail_pnl is not None and peak > 0 and peak - position.trail_pnl > stop:
        stop, reason = peak - position.trail_pnl, "trailing_stop"
    return stop, reason


def _leg_levels(leg: PaperLeg) -> tuple[float | None, float | None]:
    """The option prices at which a filled leg's own stop and target trigger."""

    def distance(amount: float | None, unit: str) -> float | None:
        if amount is None:
            return None
        return leg.entry_price * amount / 100 if unit == "pct" else amount

    stop, target = distance(leg.stop, leg.stop_unit), distance(leg.target, leg.target_unit)
    if leg.side > 0:
        stop_price = None if stop is None else leg.entry_price - stop
        target_price = None if target is None else leg.entry_price + target
    else:
        stop_price = None if stop is None else leg.entry_price + stop
        target_price = None if target is None else leg.entry_price - target
    if stop_price is not None and stop_price <= 0:
        stop_price = None
    if target_price is not None and target_price <= 0:
        target_price = None
    return stop_price, target_price


def _settlement_moment(expiry: date) -> datetime:
    return datetime.combine(expiry, SETTLEMENT_TIME, tzinfo=IST)


def _usable(value) -> bool:
    return value is not None and not pd.isna(value)


def _quote_book(chains: dict[str, pd.DataFrame]) -> dict[str, dict]:
    book: dict[str, dict] = {}
    for chain in chains.values():
        if not chain.empty:
            book.update(chain.set_index("instrument_key")[QUOTE_COLUMNS].to_dict("index"))
    return book


def _intrinsic(leg: PaperLeg, spot: float) -> float:
    return max(spot - leg.strike, 0.0) if leg.option_type == "CE" else max(leg.strike - spot, 0.0)


class PaperBroker:
    def __init__(self, ledger: Ledger, risk: RiskConfig, costs: CostModel):
        self.ledger = ledger
        self.risk = risk
        self.costs = costs
        self.paper = risk.paper

    def submit(self, idea, now: datetime) -> int:
        """Queue a tradable idea to fill on the next snapshot."""
        if not idea.tradable:
            raise ValueError("Only tradable ideas can be submitted")
        lots = idea.risk.lots
        legs = [
            PaperLeg(
                instrument_key=leg.instrument_key,
                expiry=leg.expiry.isoformat(),
                strike=leg.strike,
                option_type=leg.option_type,
                side=leg.side,
                units=leg.units * lots,
                delta=leg.delta,
                gamma=leg.gamma,
                vega=leg.vega,
                stop=getattr(leg, "stop", None),
                stop_unit=getattr(leg, "stop_unit", "pct"),
                target=getattr(leg, "target", None),
                target_unit=getattr(leg, "target_unit", "pct"),
            )
            for leg in idea.candidate.legs
        ]
        position = Position(
            symbol=idea.candidate.symbol,
            strategy=idea.candidate.strategy,
            profile=idea.candidate.profile,
            lots=lots,
            created_at=now.isoformat(),
            target_pnl=idea.plan.target_pnl * lots,
            stop_pnl=idea.plan.stop_pnl * lots,
            exit_dte=idea.plan.exit_dte,
            planned_loss=idea.risk.planned_loss,
            margin=idea.risk.margin,
            score=idea.final_score,
            expected_entry_debit=idea.structure.entry_debit * lots,
            last_spot=idea.ctx_spot,
            legs=legs,
            trail_pnl=None if idea.plan.trail is None else idea.plan.trail * lots,
            breakeven_pnl=None if idea.plan.breakeven_at is None else idea.plan.breakeven_at * lots,
            case=getattr(idea.candidate, "case", 0),
            why=list(getattr(idea.candidate, "why", ())),
        )
        position_id = self.ledger.save(position)
        self.ledger.log(now, position_id, "submitted", f"{position.strategy} x{lots}")
        return position_id

    def step(self, now: datetime, chains: dict[str, pd.DataFrame]) -> StepReport:
        """Apply prior decisions at these quotes, then make new decisions from them."""
        quotes = _quote_book(chains)
        spots = {s: float(c["spot"].iloc[0]) for s, c in chains.items() if not c.empty}
        report = StepReport()
        halted = self._is_halted(now)
        for position in self.ledger.positions((PENDING,)):
            self._try_entry(position, now, quotes, halted, report, spots.get(position.symbol))
        for position in self.ledger.positions((OPEN,)):
            self._fill_leg_exits(position, now, quotes, report)
        for position in self.ledger.positions((EXITING,)):
            self._try_exit(position, now, quotes, report)
        for position in self.ledger.positions((OPEN, EXITING)):
            self._try_settle(position, now, quotes, report)
        for position in self.ledger.positions((OPEN, EXITING)):
            self._mark(position, now, quotes, spots, report)
        report.halted = self._record_equity_and_halt(now, report)
        return report

    def fill_exits(self, now: datetime, chains: dict[str, pd.DataFrame]) -> StepReport:
        """Close positions whose exit was just triggered, at these quotes. Entries wait.

        Intraday replays use this so a stop, target or square-off seen at a decision time is
        acted on then, not at the next decision."""
        quotes = _quote_book(chains)
        report = StepReport()
        for position in self.ledger.positions((OPEN,)):
            self._fill_leg_exits(position, now, quotes, report)
        for position in self.ledger.positions((EXITING,)):
            self._try_exit(position, now, quotes, report)
        self._record_equity_and_halt(now, report)
        return report

    def check_intrabar(self, now: datetime, extremes, report: StepReport) -> None:
        """Trigger exits from a bar's extreme P&L. A stop wins when both levels were reachable.

        `extremes` returns (lowest, highest) P&L for a position, or None when it cannot be valued.
        """
        for position in self.ledger.positions((OPEN,)):
            values = extremes(position)
            if values is None:
                continue
            if len(values) > 2 and self._trigger_legs(position, now, values[2]):
                self.ledger.save(position)
            stop, reason = effective_stop(position)
            if values[0] <= stop:
                self._begin_exit(position, now, reason, report)
            elif values[1] >= position.target_pnl:
                self._begin_exit(position, now, "target", report)

    def request_exit(self, position_id: int, now: datetime, reason: str) -> bool:
        """Start closing an open position, for example when an exit rule fires."""
        for position in self.ledger.positions((OPEN,)):
            if position.id == position_id:
                self._begin_exit(position, now, reason, StepReport())
                return True
        return False

    def flatten(self, now: datetime, chains: dict[str, pd.DataFrame]) -> StepReport:
        """Close everything at these quotes, for the end of a backtest."""
        report = StepReport()
        quotes = _quote_book(chains)
        for position in self.ledger.positions((PENDING,)):
            self._cancel(position, now, "end of test", report)
        for position in self.ledger.positions((OPEN, EXITING)):
            if position.status == OPEN:
                self._begin_exit(position, now, "end_of_test", report)
        for position in self.ledger.positions((EXITING,)):
            self._try_exit(position, now, quotes, report)
        for position in self.ledger.positions((EXITING,)):
            self._try_settle(position, now, quotes, report)
        for position in self.ledger.positions((EXITING,)):
            self._close_at_marks(position, now, report)
        self._record_equity_and_halt(now, report)
        return report

    def _close_at_marks(self, position, now, report: StepReport) -> None:
        """Last resort when a position has no quote: close at its last known value."""
        position.exit_reason = "end_of_test_at_last_mark"
        prices = [leg.exit_price if leg.closed else leg.mark for leg in position.legs]
        self._close(position, now, prices, 0.0, report)

    def portfolio_state(self, now: datetime) -> PortfolioState:
        state = PortfolioState(
            capital=self.risk.capital,
            day_pnl=self._current_pnl() - self._baseline(now),
            halted=self._is_halted(now),
        )
        for position in self.ledger.positions(LIVE_STATUSES):
            state.position_count += 1
            state.open_risk += position.planned_loss
            state.margin_used += position.margin
            state.risk_by_symbol[position.symbol] = (
                state.risk_by_symbol.get(position.symbol, 0.0) + position.planned_loss
            )
            state.open_keys.add(
                (position.symbol, position.strategy, position.front_expiry.isoformat())
            )
            spot = position.last_spot
            gamma = 0.0
            for leg in position.legs:
                if leg.closed:
                    continue
                state.delta_notional += leg.side * leg.units * leg.delta * spot
                state.vega += leg.side * leg.units * leg.vega
                gamma += leg.side * leg.units * leg.gamma
            state.gamma_1pct += 0.5 * abs(gamma) * (spot * 0.01) ** 2
        return state

    def _is_halted(self, now: datetime) -> bool:
        return self.ledger.get_meta(HALT_KEY) == now.date().isoformat()

    def _baseline(self, now: datetime) -> float:
        return self.ledger.baseline_equity(now.date()) or 0.0

    def _current_pnl(self) -> float:
        open_pnl = sum(
            p.unrealized_pnl - p.entry_costs - p.exit_costs
            for p in self.ledger.positions((OPEN, EXITING))
        )
        return self.ledger.realized_total() + open_pnl

    def _record_equity_and_halt(self, now: datetime, report: StepReport) -> bool:
        realized = self.ledger.realized_total()
        unrealized = self._current_pnl() - realized
        self.ledger.record_equity(now, realized, unrealized)
        day_pnl = realized + unrealized - self._baseline(now)
        if day_pnl > -self.risk.amount(self.risk.max_daily_loss_pct):
            return self._is_halted(now)
        if not self._is_halted(now):
            self.ledger.set_meta(HALT_KEY, now.date().isoformat())
            self.ledger.log(now, None, "halted", f"day P&L {day_pnl:.2f}")
        for position in self.ledger.positions((OPEN,)):
            self._begin_exit(position, now, "daily_loss_halt", report)
        return True

    def _cancel(self, position: Position, now: datetime, reason: str, report: StepReport) -> None:
        position.status = CANCELLED
        position.closed_at = now.isoformat()
        position.exit_reason = reason
        self.ledger.save(position)
        self.ledger.log(now, position.id, "cancelled", reason)
        report.cancelled.append(position.id)

    def _try_entry(
        self, position, now, quotes, halted: bool, report: StepReport, spot: float | None = None
    ) -> None:
        if halted:
            self._cancel(position, now, "daily loss limit reached", report)
            return
        expires = datetime.fromisoformat(position.created_at) + timedelta(
            minutes=self.paper.idea_ttl_minutes
        )
        if now > expires:
            self._cancel(position, now, "idea expired before a fill", report)
            return
        slip = self.paper.slippage_per_leg
        fills = []
        for leg in position.legs:
            quote = quotes.get(leg.instrument_key)
            if quote is None:
                self._cancel(position, now, f"no quote for {leg.instrument_key}", report)
                return
            price_field, depth_field = ("ask", "ask_qty") if leg.side > 0 else ("bid", "bid_qty")
            price, depth = quote[price_field], quote[depth_field]
            if not _usable(price) or price <= 0:
                self._cancel(position, now, f"one-sided quote for {leg.instrument_key}", report)
                return
            if not _usable(depth) or depth < leg.units:
                self._cancel(position, now, f"insufficient depth for {leg.instrument_key}", report)
                return
            fills.append(max(price + leg.side * slip, 0.0))
        debit = sum(
            leg.side * price * leg.units for leg, price in zip(position.legs, fills, strict=True)
        )
        expected = position.expected_entry_debit
        if abs(debit - expected) > abs(expected) * self.paper.max_entry_drift_pct / 100:
            self._cancel(position, now, f"price moved: {expected:.2f} to {debit:.2f}", report)
            return
        for leg, price in zip(position.legs, fills, strict=True):
            leg.entry_price = price
            leg.mark = price
            leg.stop_price, leg.target_price = _leg_levels(leg)
            # Delta at the fill, kept apart from `delta`, which each mark refreshes.
            delta = quotes[leg.instrument_key]["delta"]
            leg.entry_delta = float(delta) if _usable(delta) else leg.delta
            position.entry_costs += self._order_cost(position, leg, leg.side > 0, price, now)
        position.status = OPEN
        position.opened_at = now.isoformat()
        position.entry_spot = spot if spot is not None else position.last_spot
        self.ledger.save(position)
        self.ledger.log(now, position.id, "opened", f"net debit {debit:.2f}")
        report.opened.append(position.id)

    def _order_cost(self, position, leg: PaperLeg, is_buy: bool, price: float, now) -> float:
        """Charges for one leg's order, split into slices at the freeze quantity."""
        slices = self.risk.order_slices(position.symbol, leg.units)
        return self.costs.order(is_buy, price, leg.units, now.date(), slices)

    def _exit_quote(self, leg: PaperLeg, quotes: dict) -> float | None:
        quote = quotes.get(leg.instrument_key)
        if quote is None:
            return None
        price = quote["bid"] if leg.side > 0 else quote["ask"]
        if not _usable(price) or price <= 0:
            return None
        return max(price - leg.side * self.paper.slippage_per_leg, 0.0)

    def _try_exit(self, position, now, quotes, report: StepReport) -> None:
        """Close every leg at one snapshot, or wait until all legs have quotes. Legs that
        already closed on their own stop or target keep their fill."""
        prices = [
            leg.exit_price if leg.closed else self._exit_quote(leg, quotes) for leg in position.legs
        ]
        if any(price is None for price in prices):
            return
        costs = sum(
            self._order_cost(position, leg, leg.side < 0, price, now)
            for leg, price in zip(position.legs, prices, strict=True)
            if not leg.closed
        )
        self._close(position, now, prices, costs, report)

    def _try_settle(self, position, now, quotes, report: StepReport) -> None:
        if now < _settlement_moment(position.front_expiry):
            return
        prices: list[float] = []
        costs = 0.0
        expiry_day = position.front_expiry
        estimated = False
        for leg in position.legs:
            if leg.closed:
                prices.append(leg.exit_price)
                continue
            if date.fromisoformat(leg.expiry) == expiry_day:
                intrinsic = _intrinsic(leg, position.last_spot)
                prices.append(intrinsic)
                estimated = True
                if leg.side > 0 and intrinsic > 0:
                    costs += self.costs.exercise(intrinsic, leg.units, expiry_day)
                continue
            price = self._exit_quote(leg, quotes)
            if price is None:
                return
            prices.append(price)
            costs += self._order_cost(position, leg, leg.side < 0, price, now)
        position.settlement_estimated = estimated
        if position.exit_reason is None:
            position.exit_reason = "expiry_settlement"
        self._close(position, now, prices, costs, report)

    def _close(self, position, now, prices, exit_costs: float, report: StepReport) -> None:
        gross = 0.0
        for leg, price in zip(position.legs, prices, strict=True):
            leg.exit_price = price
            leg.mark = price
            gross += leg.side * (price - leg.entry_price) * leg.units
        position.exit_costs += exit_costs
        position.realized_pnl = gross - position.entry_costs - position.exit_costs
        position.unrealized_pnl = 0.0
        position.status = CLOSED
        position.closed_at = now.isoformat()
        self.ledger.save(position)
        self.ledger.log(
            now, position.id, "closed", f"{position.exit_reason}: {position.realized_pnl:.2f}"
        )
        report.closed.append(position.id)

    def _trigger_legs(self, position, now: datetime, ranges) -> bool:
        """Mark legs whose own stop or target was reached, given each leg's (low, high) price
        or None. A stop wins when both were reachable. The legs fill at the next quotes."""
        changed = False
        for leg, prices in zip(position.legs, ranges, strict=True):
            if prices is None or leg.exit_reason is not None or leg.entry_price is None:
                continue
            low, high = prices
            if low is None or high is None:
                continue
            worst, best = (low, high) if leg.side > 0 else (high, low)
            hit_stop = leg.stop_price is not None and (
                worst <= leg.stop_price if leg.side > 0 else worst >= leg.stop_price
            )
            hit_target = leg.target_price is not None and (
                best >= leg.target_price if leg.side > 0 else best <= leg.target_price
            )
            if hit_stop or hit_target:
                leg.exit_reason = "leg_stop" if hit_stop else "leg_target"
                self.ledger.log(
                    now,
                    position.id,
                    "leg_exit_triggered",
                    f"{leg.option_type} {leg.strike:g} {leg.exit_reason}",
                )
                changed = True
        return changed

    def _fill_leg_exits(self, position, now, quotes, report: StepReport) -> None:
        """Close legs that hit their own stop or target. When none is left open, the position
        closes with the last leg's reason."""
        waiting = [leg for leg in position.legs if leg.exit_reason and leg.exit_price is None]
        if not waiting:
            return
        for leg in waiting:
            price = self._exit_quote(leg, quotes)
            if price is None:
                continue
            leg.exit_price = price
            leg.mark = price
            leg.closed_at = now.isoformat()
            position.exit_costs += self._order_cost(position, leg, leg.side < 0, price, now)
            self.ledger.log(now, position.id, "leg_closed", f"{leg.exit_reason} at {price:.2f}")
        if all(leg.closed for leg in position.legs):
            position.exit_reason = max(
                position.legs, key=lambda leg: leg.closed_at or ""
            ).exit_reason
            self._close(position, now, [leg.exit_price for leg in position.legs], 0.0, report)
        else:
            self.ledger.save(position)

    def _begin_exit(self, position, now, reason: str, report: StepReport) -> None:
        position.status = EXITING
        position.exit_reason = reason
        self.ledger.save(position)
        self.ledger.log(now, position.id, "exit_triggered", reason)
        report.exits_triggered.append(position.id)

    def _square_off_due(self, position: Position, now: datetime) -> bool:
        rule = self.risk.exits.get(position.profile)
        square_off = getattr(rule, "square_off", None)
        return square_off is not None and now.time() >= square_off

    def _time_exit_due(self, position: Position, now: datetime) -> bool:
        exit_day = position.front_expiry - timedelta(days=position.exit_dte)
        today = now.date()
        return today > exit_day or (today == exit_day and now.time() >= TIME_EXIT)

    def _mark(self, position, now, quotes, spots, report: StepReport) -> None:
        if position.symbol in spots and now <= _settlement_moment(position.front_expiry):
            position.last_spot = spots[position.symbol]
        stale = False
        for leg in position.legs:
            if leg.closed:
                continue
            quote = quotes.get(leg.instrument_key)
            mid = None if quote is None else quote["mid"]
            if not _usable(mid) or math.isnan(float(mid)):
                stale = True
                continue
            leg.mark = float(mid)
            for greek in ("delta", "gamma", "vega"):
                if _usable(quote[greek]):
                    setattr(leg, greek, float(quote[greek]))
        position.stale = stale
        position.unrealized_pnl = sum(
            leg.side * (leg.mark - leg.entry_price) * leg.units for leg in position.legs
        )
        self.ledger.save(position)
        if position.status != OPEN:
            return
        if not stale:
            position.peak_pnl = max(position.peak_pnl, position.unrealized_pnl)
            self.ledger.save(position)
        if self._trigger_legs(position, now, [(leg.mark, leg.mark) for leg in position.legs]):
            self.ledger.save(position)
        stop, reason = effective_stop(position)
        if not stale and position.unrealized_pnl >= position.target_pnl:
            self._begin_exit(position, now, "target", report)
        elif not stale and position.unrealized_pnl <= stop:
            self._begin_exit(position, now, reason, report)
        elif self._square_off_due(position, now):
            self._begin_exit(position, now, "square_off", report)
        elif self._time_exit_due(position, now):
            self._begin_exit(position, now, "time", report)

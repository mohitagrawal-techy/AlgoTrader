"""The worked examples in the strategy builder's feature cards, run through the engine.

Each card buys one lot (65) of a Nifty call at ₹100, so a point of option price is ₹65.
Quotes here have no spread, so the marked P&L moves point for point with the price."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from algoverse.execution.ledger import CLOSED, EXITING, OPEN
from algoverse.execution.paper import effective_stop
from algoverse.lab.spec import STOPPED_OUT, TARGET_HIT, CustomSet, _Wait
from algoverse.risk.limits import ExitPlan
from conftest import NOW, later
from test_cases import make
from test_lab import SPREAD
from test_paper import EXPIRY, KEY, broker, idea, leg, one  # noqa: F401

LOT = 65
POINT = 65.0


def quote(price: float) -> dict:
    row = {
        "instrument_key": KEY,
        "bid": price,
        "ask": price,
        "mid": price,
        "bid_qty": 650.0,
        "ask_qty": 650.0,
        "delta": 0.5,
        "gamma": 0.0005,
        "vega": 10.0,
        "spot": 24000.0,
    }
    return {"NIFTY": pd.DataFrame([row])}


def bought(broker, trail=None, breakeven=None):  # noqa: F811
    """One lot bought, with a 20-point stop. Returns the position and its fill price."""
    plan = ExitPlan(1e6, -20 * POINT, 1, 20 * POINT, trail, breakeven)
    order = idea()
    order.plan = plan
    broker.submit(order, NOW)
    broker.step(later(5), quote(100.0))
    position = one(broker, OPEN)
    return position, position.legs[0].entry_price


def walk(broker, entry, moves):  # noqa: F811
    """Marks the position at entry + each move in points; returns the stop after each."""
    stops = []
    for minute, move in enumerate(moves, start=10):
        broker.step(later(minute), quote(entry + move))
        position = broker.ledger.positions((OPEN, EXITING, CLOSED))[0]
        stops.append((position.status, effective_stop(position)))
    return stops


def test_trailing_stop_card(broker):  # noqa: F811
    position, entry = bought(broker, trail=10 * POINT)
    # ₹105, ₹110, ₹125, ₹130 on a ₹100 entry: the stop sits at ₹95, ₹100, ₹115, ₹120.
    stops = walk(broker, entry, [5, 10, 25, 30])
    assert [status for status, _ in stops] == [OPEN] * 4
    assert [stop for _, (stop, _) in stops] == pytest.approx(
        [-5 * POINT, 0.0, 15 * POINT, 20 * POINT]
    )
    assert all(reason == "trailing_stop" for _, (_, reason) in stops)
    # Falls to ₹120: the stop is hit and the trade exits with +20 points.
    walk(broker, entry, [20])
    position = one(broker, EXITING)
    assert position.exit_reason == "trailing_stop"
    broker.step(later(30), quote(entry + 20))
    position = one(broker, CLOSED)
    gross = sum(lg.side * (lg.exit_price - lg.entry_price) * lg.units for lg in position.legs)
    assert gross == pytest.approx(20 * POINT - broker.paper.slippage_per_leg * LOT)


def test_trailing_stop_never_loosens(broker):  # noqa: F811
    _, entry = bought(broker, trail=10 * POINT)
    stops = walk(broker, entry, [30, 25])
    assert stops[1][1][0] == pytest.approx(20 * POINT)  # Still ₹120 after the dip to ₹125.


def test_move_to_cost_card(broker):  # noqa: F811
    _, entry = bought(broker, breakeven=15 * POINT)
    # ₹108: not moved. ₹115: moved to the entry. ₹125: stays at the entry.
    stops = walk(broker, entry, [8, 15, 25])
    assert stops == [
        (OPEN, (-20 * POINT, "stop")),
        (OPEN, (0.0, "stop_at_cost")),
        (OPEN, (0.0, "stop_at_cost")),
    ]
    # Falls back to ₹100: it exits at cost rather than riding down to the ₹80 stop.
    walk(broker, entry, [0])
    assert one(broker, EXITING).exit_reason == "stop_at_cost"


def test_without_move_to_cost_the_same_path_stays_open(broker):  # noqa: F811
    _, entry = bought(broker)
    assert walk(broker, entry, [8, 15, 25, 0])[-1] == (OPEN, (-20 * POINT, "stop"))


def test_with_both_the_higher_stop_applies(broker):  # noqa: F811
    _, entry = bought(broker, trail=10 * POINT, breakeven=15 * POINT)
    stops = walk(broker, entry, [15, 30])
    assert stops[0][1] == (pytest.approx(5 * POINT), "trailing_stop")
    assert stops[1][1] == (pytest.approx(20 * POINT), "trailing_stop")


def closed(reason: str, day):
    return SimpleNamespace(
        strategy="s1",
        status="closed",
        created_at=f"{day}T09:45:00+05:30",
        closed_at=f"{day}T10:30:00+05:30",
        exit_reason=reason,
        realized_pnl=-1300.0,
    )


def limits_after(reason: str, **entry):
    spec = make(entry={**SPREAD["entry"], "max_trades_per_day": 3, **entry})
    custom = CustomSet([spec])
    day = NOW.date()
    custom.broker = SimpleNamespace(
        ledger=SimpleNamespace(positions=lambda statuses: [closed(reason, day)])
    )
    return custom.limit_failures(spec, day)


@pytest.mark.parametrize("reason", ["stop", "trailing_stop", "stop_at_cost", "leg_stop"])
def test_re_entry_card(reason):
    # Every kind of stop the card lists counts as a stop.
    assert reason in STOPPED_OUT
    assert limits_after(reason) == []
    assert limits_after(reason, reenter_after_stop=False) == [
        "entry rule: no re-entry after a stop today"
    ]
    # A stop does not block re-execute, and the reverse.
    assert limits_after(reason, reenter_after_target=False) == []


@pytest.mark.parametrize("reason", ["target", "leg_target"])
def test_re_execute_card(reason):
    assert reason in TARGET_HIT
    assert limits_after(reason) == []
    assert limits_after(reason, reenter_after_target=False) == [
        "entry rule: no new trade after a target today"
    ]
    assert limits_after(reason, reenter_after_stop=False) == []


def test_re_entries_still_count_towards_the_day_limit():
    assert limits_after("stop", max_trades_per_day=1)


def chain(price: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "instrument_key": KEY,
                "expiry": EXPIRY,
                "strike": 24000.0,
                "option_type": "CE",
                "lot_size": LOT,
                "bid": price,
                "ask": price,
                "mid": price,
                "iv": 0.14,
                "delta": 0.5,
                "gamma": 0.0005,
                "theta": -5.0,
                "vega": 10.0,
                "oi": 1e5,
                "volume": 1e5,
                "T": 12 / 365,
                "forward": 24050.0,
            }
        ]
    )


@pytest.mark.parametrize(
    "direction, path, buys_at",
    [
        # The card: rules hold at ₹100, wait for up 10%: 104 and 97 wait, 111 buys.
        ("up", [104.0, 97.0, 111.0], 111.0),
        # Down 10%: it buys only once the price falls to ₹90.
        ("down", [104.0, 95.0, 89.0], 89.0),
    ],
)
def test_wait_and_trade_card(direction, path, buys_at):
    spec = make(
        entry={**SPREAD["entry"], "wait_move": 10, "wait_unit": "pct", "wait_direction": direction}
    )
    custom = CustomSet([spec])
    signal = SimpleNamespace(legs=(replace(leg(), mid=100.0),))
    from algoverse.strategies.base import Candidate

    picked = Candidate("NIFTY", "s1", "p", "bullish", signal.legs)
    key = (spec.id, "NIFTY", 0)
    custom.waiting[key] = _Wait(NOW.date(), picked, 100.0)
    for price in path:
        [(candidate, failures)] = custom._waited(key, custom.waiting[key], spec, chain(price), [])
        # It watches the same contract it picked when the rules held.
        assert candidate.legs[0].instrument_key == KEY
        if price == buys_at:
            assert failures == [] and key not in custom.waiting
            assert candidate.legs[0].mid == buys_at
        else:
            assert failures and failures[0].startswith("wait and trade") and key in custom.waiting


def test_a_wait_ends_with_the_day():
    spec = make(entry={**SPREAD["entry"], "wait_move": 10})
    custom = CustomSet([spec])
    from algoverse.strategies.base import Candidate

    key = (spec.id, "NIFTY", 0)
    yesterday = NOW.date() - timedelta(days=1)
    custom.waiting[key] = _Wait(yesterday, Candidate("NIFTY", "s1", "p", "bullish", (leg(),)), 1.0)
    ctx = SimpleNamespace(symbol="NIFTY", now=NOW)
    # A new day drops yesterday's wait before anything else; with no live contracts no new
    # one is armed, so nothing is bought at yesterday's price.
    custom.candidates(ctx, chain(500.0).assign(T=0.0))
    assert key not in custom.waiting

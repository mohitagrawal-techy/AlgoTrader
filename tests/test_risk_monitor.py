from datetime import datetime, timedelta

from algoverse.analytics.chain import IST
from algoverse.analytics.pricing import black76_price
from algoverse.execution.ledger import PaperLeg, Position
from algoverse.risk.monitor import exposure_by_symbol, portfolio_stress

NOW = datetime(2026, 11, 13, 11, 0, tzinfo=IST)
EXPIRY = (NOW + timedelta(days=7)).date()


def _position(symbol, side, mark, delta=0.5, gamma=0.001, vega=10.0, planned=1000.0):
    leg = PaperLeg(
        instrument_key=f"{symbol}-24000-CE",
        expiry=EXPIRY.isoformat(),
        strike=24000.0,
        option_type="CE",
        side=side,
        units=75,
        entry_price=mark,
        mark=mark,
        delta=delta,
        gamma=gamma,
        vega=vega,
    )
    return Position(
        symbol=symbol,
        strategy="long_call",
        profile="directional_buy",
        lots=1,
        created_at=NOW.isoformat(),
        target_pnl=1000,
        stop_pnl=-1000,
        exit_dte=1,
        planned_loss=planned,
        margin=0,
        score=60,
        expected_entry_debit=mark,
        last_spot=24000.0,
        legs=[leg],
    )


def test_exposure_groups_by_symbol(risk_config):
    rows = exposure_by_symbol(
        [
            _position("NIFTY", 1, 200.0),
            _position("NIFTY", -1, 200.0),
            _position("BANKNIFTY", 1, 300.0, planned=5000),
        ],
        risk_config,
    )
    assert [row["symbol"] for row in rows] == ["BANKNIFTY", "NIFTY"]
    nifty = rows[1]
    assert nifty["positions"] == 2 and nifty["planned_risk"] == 2000
    assert nifty["delta_notional"] == 0 and nifty["vega"] == 0


def test_stress_is_zero_at_no_move_and_signed(risk_config):
    T = 7 / 365 - 4.5 / 24 / 365
    mark = float(black76_price(24000.0, 24000.0, T, 0.15, 0.065, True))
    stress = portfolio_stress([_position("NIFTY", 1, mark)], risk_config, 0.065, NOW)
    moves, shifts, pnl = stress["spot_moves_pct"], stress["iv_shifts_pct"], stress["pnl"]
    zero_row = pnl[moves.index(0.0)]
    assert abs(zero_row[shifts.index(0.0)]) < 1
    assert pnl[moves.index(max(moves))][0] > 0 > pnl[moves.index(min(moves))][0]
    assert stress["approximated_legs"] == 0


def test_stress_falls_back_to_greeks_without_mark(risk_config):
    position = _position("NIFTY", -1, 200.0)
    position.legs[0].mark = None
    stress = portfolio_stress([position], risk_config, 0.065, NOW)
    assert stress["approximated_legs"] == 1
    up = stress["pnl"][-1][0]
    assert up < 0


def test_risk_engine_blocks_unlimited_risk_by_default(risk_config):
    assert risk_config.allow_unlimited_risk is False

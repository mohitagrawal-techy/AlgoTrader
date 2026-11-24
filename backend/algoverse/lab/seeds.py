"""Standard structures, so every leaderboard has familiar baselines."""

from __future__ import annotations

from algoverse.lab.spec import StrategySpec


def _spec(id_, name, bias, legs, entry, exits, rationale) -> StrategySpec:
    keys = ("action", "option_type", "select", "value", "ratio", "expiry")
    return StrategySpec(
        id=id_,
        name=name,
        rationale=rationale,
        source="template",
        bias=bias,
        legs=[dict(zip(keys, leg, strict=False)) for leg in legs],
        entry=entry,
        exits=dict(zip(("target_frac", "stop_frac", "exit_dte"), exits, strict=True)),
    )


DEBIT = (0.6, 0.5, 1)
CREDIT = (0.5, 1.0, 1)

TEMPLATES: list[StrategySpec] = [
    _spec(
        "tpl_long_call",
        "Long call",
        "bullish",
        [("buy", "CE", "delta", 0.5)],
        {"trend": ["up"], "iv_rank_max": 50, "dte_min": 5},
        DEBIT,
        "Buys a call in an uptrend when options are not expensive.",
    ),
    _spec(
        "tpl_long_put",
        "Long put",
        "bearish",
        [("buy", "PE", "delta", 0.5)],
        {"trend": ["down"], "iv_rank_max": 50, "dte_min": 5},
        DEBIT,
        "Buys a put in a downtrend when options are not expensive.",
    ),
    _spec(
        "tpl_bull_call_spread",
        "Bull call spread",
        "bullish",
        [("buy", "CE", "delta", 0.5), ("sell", "CE", "delta", 0.3)],
        {"trend": ["up"], "dte_min": 5},
        DEBIT,
        "Defined-risk bullish spread, taken only in an uptrend.",
    ),
    _spec(
        "tpl_bear_put_spread",
        "Bear put spread",
        "bearish",
        [("buy", "PE", "delta", 0.5), ("sell", "PE", "delta", 0.3)],
        {"trend": ["down"], "dte_min": 5},
        DEBIT,
        "Defined-risk bearish spread, taken only in a downtrend.",
    ),
    _spec(
        "tpl_bull_put_credit",
        "Bull put credit spread",
        "bullish",
        [("sell", "PE", "delta", 0.25), ("buy", "PE", "delta", 0.12)],
        {"trend": ["up", "range"], "iv_rank_min": 30, "dte_min": 5},
        CREDIT,
        "Sells a put spread below the market when volatility is not low.",
    ),
    _spec(
        "tpl_bear_call_credit",
        "Bear call credit spread",
        "bearish",
        [("sell", "CE", "delta", 0.25), ("buy", "CE", "delta", 0.12)],
        {"trend": ["down", "range"], "iv_rank_min": 30, "dte_min": 5},
        CREDIT,
        "Sells a call spread above the market when volatility is not low.",
    ),
    _spec(
        "tpl_iron_condor",
        "Iron condor",
        "neutral",
        [
            ("buy", "PE", "delta", 0.08),
            ("sell", "PE", "delta", 0.2),
            ("sell", "CE", "delta", 0.2),
            ("buy", "CE", "delta", 0.08),
        ],
        {"trend": ["range"], "iv_rank_min": 30, "dte_min": 5},
        CREDIT,
        "Collects premium on both sides in a range-bound market.",
    ),
    _spec(
        "tpl_call_butterfly",
        "Call butterfly",
        "neutral",
        [
            ("buy", "CE", "atm_offset", -4),
            ("sell", "CE", "atm_offset", 0, 2),
            ("buy", "CE", "atm_offset", 4),
        ],
        {"trend": ["range"], "dte_min": 3},
        (1.0, 0.5, 0),
        "Low-cost bet that the index stays near its current level.",
    ),
    _spec(
        "tpl_long_straddle",
        "Long straddle",
        "neutral",
        [("buy", "CE", "atm_offset", 0), ("buy", "PE", "atm_offset", 0)],
        {"iv_rank_max": 30, "dte_min": 7},
        (0.5, 0.4, 1),
        "Buys volatility when it is cheap compared with its own history.",
    ),
    _spec(
        "tpl_call_calendar",
        "Call calendar",
        "neutral",
        [("sell", "CE", "atm_offset", 0, 1, 0), ("buy", "CE", "atm_offset", 0, 1, 1)],
        {"trend": ["range"], "dte_min": 3},
        (0.25, 0.3, 1),
        "Sells the near expiry against the next one in a quiet market.",
    ),
]

"""Where a lab strategy stands on the research ladder, from the evidence its backtest left.

REJECT          No edge: it lost on unseen data, lost its profit at double slippage, or takes
                unlimited risk while risk.yaml forbids it.
RESEARCH        A hypothesis still being tested: too few unseen trades, a profit luck could
                explain, a loss in training, or profit in too few of the consecutive periods.
PAPER TRADE     Passed every check the lab can run.
APPROVED        Passed forward paper trading as well. Lab strategies are not paper traded live
                yet, so nothing reaches this stage.
LIVE MONITORING Approved for controlled live deployment. The app places no real orders.

The stage is worked out when results are read, so changing a threshold in backtest.yaml
re-grades every strategy without testing anything again.
"""

from __future__ import annotations

from algoverse.backtest.config import LabConfig
from algoverse.lab.evaluate import FAILED, FAILS_2X, TOO_FEW, UNPROVEN

REJECT = "reject"
RESEARCH = "research"
PAPER_TRADE = "paper_trade"
APPROVED = "approved"
LIVE_MONITORING = "live_monitoring"

STAGES = (
    {"key": REJECT, "label": "Reject", "meaning": "No statistically meaningful edge."},
    {"key": RESEARCH, "label": "Research", "meaning": "Promising, needs more testing."},
    {"key": PAPER_TRADE, "label": "Paper trade", "meaning": "Passed every lab check."},
    {
        "key": APPROVED,
        "label": "Approved",
        "meaning": "Passed forward paper trading. Not reachable yet: lab strategies are not "
        "paper traded live.",
    },
    {
        "key": LIVE_MONITORING,
        "label": "Live monitoring",
        "meaning": "Approved for controlled deployment. Not reachable: the app places no "
        "real orders.",
    },
)


def consecutive_folds(detail: dict | None, count: int) -> list[dict]:
    """Split the training and test sessions, in order, into `count` periods of equal length.

    Each period reports its net P&L and the trades that closed in it. The strategy's rules are
    the same in every period, so this shows whether its results depend on one stretch of
    the market.
    """
    if not detail or count < 1:
        return []
    daily: dict[str, float] = {}
    trades: list[dict] = []
    for window in ("train", "test"):
        part = detail.get(window) or {}
        daily.update(part.get("daily") or {})
        trades += part.get("trades") or []
    days = sorted(daily)
    if len(days) < count:
        return []
    size = len(days) / count
    folds = []
    for index in range(count):
        chunk = days[round(index * size) : round((index + 1) * size)]
        start, end = chunk[0], chunk[-1]
        closed = [t for t in trades if start <= str(t.get("closed", ""))[:10] <= end]
        folds.append(
            {
                "start": start,
                "end": end,
                "sessions": len(chunk),
                "net_pnl": float(sum(daily[day] for day in chunk)),
                "trades": len(closed),
            }
        )
    return folds


def rupees(value: float | None) -> str:
    """Whole rupees with Indian digit grouping: -6,52,188 rather than -652,188."""
    if value is None:
        return "–"
    sign = "−" if value < 0 else ""
    digits = str(round(abs(value)))
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return f"{sign}₹{','.join([*groups, tail])}"


def _check(key: str, label: str, passed: bool, detail: str) -> dict:
    return {"key": key, "label": label, "passed": bool(passed), "detail": detail}


def grade(
    result: dict | None,
    unlimited_risk: bool,
    allow_unlimited_risk: bool,
    config: LabConfig,
    capital: float,
) -> dict:
    """The stage, the reason for it, and every check behind it, passed or not."""
    if unlimited_risk and not allow_unlimited_risk:
        return {
            "stage": REJECT,
            "reason": "Its loss has no cap, and risk.yaml does not allow unlimited risk.",
            "checks": [
                _check("bounded", "Loss is capped", False, "Unlimited-risk structure"),
            ],
            "folds": [],
        }
    if result is None:
        return {"stage": None, "reason": "Not tested yet.", "checks": [], "folds": []}

    test, train = result.get("test") or {}, result.get("train") or {}
    doubled = result.get("test_2x") or {}
    evidence = result.get("evidence") or {}
    folds = consecutive_folds(result.get("detail"), config.stage_folds)
    active = [fold for fold in folds if fold["trades"] > 0]
    positive = [fold for fold in active if fold["net_pnl"] > 0]
    share = len(positive) / len(active) if active else 0.0
    drawdown = test.get("max_drawdown_pct")
    if drawdown is None and test.get("max_drawdown") is not None and capital > 0:
        drawdown = test["max_drawdown"] / capital * 100
    floor = evidence.get("floor")

    checks = [
        _check(
            "trades",
            "Enough unseen trades",
            test.get("trades", 0) >= config.min_test_trades,
            f"{test.get('trades', 0)} of {config.min_test_trades} needed",
        ),
        _check(
            "unseen",
            "Profitable on unseen data after costs",
            (test.get("net_pnl") or 0) > 0,
            f"Net {rupees(test.get('net_pnl', 0))}",
        ),
        _check(
            "slippage",
            "Still profitable at 2x slippage",
            (doubled.get("net_pnl") or 0) > 0,
            f"Net {rupees(doubled.get('net_pnl') or 0)}",
        ),
        _check(
            "luck",
            "Profit beyond what luck explains",
            floor is not None and floor > 0,
            "Not measured" if floor is None else f"Luck-adjusted profit per trade {rupees(floor)}",
        ),
        _check(
            "training",
            "Profitable in training too",
            (train.get("net_pnl") or 0) > 0,
            f"Net {rupees(train.get('net_pnl', 0))}",
        ),
        _check(
            "consistency",
            f"Profitable in {config.stage_min_positive_share:.0%} of periods",
            bool(active) and share >= config.stage_min_positive_share,
            f"{len(positive)} of {len(active)} periods with trades",
        ),
        _check(
            "drawdown",
            f"Unseen drawdown under {config.stage_max_drawdown_pct:g}% of budget",
            drawdown is not None and drawdown <= config.stage_max_drawdown_pct,
            "Not measured" if drawdown is None else f"{drawdown:.1f}% of budget",
        ),
    ]
    passed = {check["key"]: check["passed"] for check in checks}

    verdict = result.get("verdict")
    if verdict == FAILED:
        stage, reason = REJECT, "It lost money on unseen data after costs."
    elif verdict == FAILS_2X:
        stage, reason = REJECT, "Its profit disappears at double the modelled slippage."
    elif verdict == TOO_FEW:
        stage, reason = RESEARCH, "Too few unseen trades to judge."
    elif verdict == UNPROVEN:
        stage, reason = RESEARCH, "The unseen profit is within what luck explains."
    elif not all(passed.values()):
        failing = next(check for check in checks if not check["passed"])
        stage, reason = RESEARCH, f"Not yet: {failing['label'].lower()} ({failing['detail']})."
    else:
        stage, reason = PAPER_TRADE, "Passed every lab check."
    return {"stage": stage, "reason": reason, "checks": checks, "folds": folds}

"""FII and DII activity from Upstox, shaped for the overview page.

Upstox publishes amounts in rupees crore, once for each trading day.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
CASH = "NSE_EQ|CASH"
INDEX_FUTURES = "NSE_FO|INDEX_FUTURES"
STOCK_FUTURES = "NSE_FO|STOCK_FUTURES"
INDEX_OPTIONS = "NSE_FO|INDEX_OPTIONS"
STOCK_OPTIONS = "NSE_FO|STOCK_OPTIONS"
FII_TYPES = [CASH, INDEX_FUTURES, STOCK_FUTURES, INDEX_OPTIONS, STOCK_OPTIONS]
SEGMENTS = (
    ("FII cash", "fii", CASH),
    ("DII cash", "dii", CASH),
    ("FII index futures", "fii", INDEX_FUTURES),
    ("FII stock futures", "fii", STOCK_FUTURES),
    ("FII index options", "fii", INDEX_OPTIONS),
    ("FII stock options", "fii", STOCK_OPTIONS),
)
RECENT_SESSIONS = 5


def _day(record: dict) -> date:
    return datetime.fromtimestamp(record["time_stamp"] / 1000, IST).date()


def _newest_first(records: list[dict] | None) -> list[dict]:
    return sorted(records or [], key=lambda record: record["time_stamp"], reverse=True)


def _net(record: dict | None) -> float | None:
    return None if record is None else record["buy_amount"] - record["sell_amount"]


def _flows(fii: list[dict], dii: list[dict]) -> list[dict]:
    """Net buying per date, oldest first. A date one side has not published shows as null."""
    by_day: dict[date, dict] = {}
    for key, records in (("fii_net", fii), ("dii_net", dii)):
        for record in records:
            by_day.setdefault(_day(record), {})[key] = _net(record)
    return [
        {"date": day, "fii_net": row.get("fii_net"), "dii_net": row.get("dii_net")}
        for day, row in sorted(by_day.items())
    ]


def _segment(label: str, kind: str, records: list[dict]) -> dict | None:
    if not records:
        return None
    latest = records[0]
    previous = records[1] if len(records) > 1 else None
    long_contracts = latest["total_long_contracts"]
    short_contracts = latest["total_short_contracts"]
    held = long_contracts + short_contracts
    is_options = kind == "fii" and label.endswith("options")
    is_derivative = latest["oi_contracts"] > 0
    return {
        "label": label,
        "date": _day(latest),
        "buy": latest["buy_amount"],
        "sell": latest["sell_amount"],
        "net": _net(latest),
        "oi_contracts": latest["oi_contracts"] if is_derivative else None,
        "oi_change": (
            latest["oi_contracts"] - previous["oi_contracts"]
            if is_derivative and previous is not None
            else None
        ),
        "long_pct": long_contracts / held * 100 if held else None,
        "call_net": (
            latest["total_call_long_contracts"] - latest["total_call_short_contracts"]
            if is_options
            else None
        ),
        "put_net": (
            latest["total_put_long_contracts"] - latest["total_put_short_contracts"]
            if is_options
            else None
        ),
    }


def _recent_total(records: list[dict]) -> float | None:
    return sum(_net(record) for record in records[:RECENT_SESSIONS]) if records else None


def build_institutions(
    fii_daily: dict[str, list[dict]],
    dii_daily: dict[str, list[dict]],
    fii_monthly: dict[str, list[dict]],
    dii_monthly: dict[str, list[dict]],
    now: datetime,
) -> dict:
    fii = {key: _newest_first(records) for key, records in fii_daily.items()}
    dii = {key: _newest_first(records) for key, records in dii_daily.items()}
    sources = {"fii": fii, "dii": dii}
    segments = [_segment(label, kind, sources[kind].get(key, [])) for label, kind, key in SEGMENTS]
    fii_cash = fii.get(CASH, [])
    dii_cash = dii.get(CASH, [])
    fii_net = _net(fii_cash[0]) if fii_cash else None
    dii_net = _net(dii_cash[0]) if dii_cash else None
    return {
        "status": "ok",
        "fetched_at": now,
        "as_of": _day(fii_cash[0]) if fii_cash else None,
        "fii_net": fii_net,
        "dii_net": dii_net,
        "combined_net": None if fii_net is None or dii_net is None else fii_net + dii_net,
        "fii_recent": _recent_total(fii_cash),
        "dii_recent": _recent_total(dii_cash),
        "daily": _flows(fii_cash, dii_cash),
        "monthly": _flows(
            _newest_first(fii_monthly.get(CASH)), _newest_first(dii_monthly.get(CASH))
        ),
        "segments": [segment for segment in segments if segment is not None],
    }


def fetch_institutions(market, now: datetime) -> dict:
    return build_institutions(
        market.institutional_flows("fii", FII_TYPES, "1D"),
        market.institutional_flows("dii", [CASH], "1D"),
        market.institutional_flows("fii", [CASH], "1M"),
        market.institutional_flows("dii", [CASH], "1M"),
        now,
    )


def empty_institutions(status: str) -> dict:
    return {
        "status": status,
        "fetched_at": None,
        "as_of": None,
        "fii_net": None,
        "dii_net": None,
        "combined_net": None,
        "fii_recent": None,
        "dii_recent": None,
        "daily": [],
        "monthly": [],
        "segments": [],
    }

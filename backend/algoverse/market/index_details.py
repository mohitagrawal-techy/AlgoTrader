"""The figures behind an index's hover card on the overview page."""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

from algoverse.analytics.regime import ema

QUICK_SESSIONS = 5
YEAR_SESSIONS = 252
VOL_SESSIONS = 20


def _constituents_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "market" / "constituents"


def _constituents_path(data_dir: Path, name: str) -> Path:
    return _constituents_dir(data_dir) / f"{re.sub(r'[^A-Za-z0-9]+', '_', name)}.json"


def save_constituents(data_dir: Path, name: str, stocks: list[dict]) -> None:
    path = _constituents_path(data_dir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(stocks))
    os.replace(tmp, path)


def load_constituents(data_dir: Path, name: str) -> list[dict]:
    try:
        return json.loads(_constituents_path(data_dir, name).read_text())
    except FileNotFoundError:
        return []


def _five_days(bars: pd.DataFrame) -> list[dict]:
    frame = bars.assign(change_pct=bars["close"].pct_change() * 100).tail(QUICK_SESSIONS)
    return frame[["date", "open", "high", "low", "close", "change_pct"]].to_dict("records")


def _stats(bars: pd.DataFrame, last: float | None, is_volatility: bool) -> dict:
    empty = dict.fromkeys(
        [
            "high_52w",
            "low_52w",
            "from_high_pct",
            "from_low_pct",
            "ema20",
            "ema50",
            "realised_vol",
            "average_range_pct",
        ]
    )
    if bars.empty or last is None:
        return empty
    year = bars.tail(YEAR_SESSIONS)
    high = float(year["high"].max())
    positive_lows = year.loc[year["low"] > 0, "low"]
    low = float(positive_lows.min()) if not positive_lows.empty else None
    closes = bars["close"].astype(float)
    returns = np.log(closes / closes.shift()).dropna().tail(VOL_SESSIONS)
    enough = len(returns) >= VOL_SESSIONS
    recent = bars.tail(VOL_SESSIONS)
    return {
        "high_52w": high,
        "low_52w": low,
        "from_high_pct": (last / high - 1) * 100 if high > 0 else None,
        "from_low_pct": (last / low - 1) * 100 if low else None,
        "ema20": float(ema(closes, 20).iloc[-1]),
        "ema50": float(ema(closes, 50).iloc[-1]),
        "realised_vol": (
            float(returns.std() * math.sqrt(YEAR_SESSIONS) * 100)
            if enough and not is_volatility
            else None
        ),
        "average_range_pct": (
            float(((recent["high"] - recent["low"]) / recent["close"]).mean() * 100)
            if enough
            else None
        ),
    }


def _members(stocks: list[dict], quotes: dict[str, dict] | None) -> dict:
    """Live moves of an index's stocks. NSE's list carries no weights, so all count equally."""
    priced = []
    for stock in stocks:
        quote = (quotes or {}).get(f"NSE_EQ|{stock['isin']}")
        if not quote or quote.get("last_price") is None or quote.get("net_change") is None:
            continue
        last, change = float(quote["last_price"]), float(quote["net_change"])
        previous = last - change
        if previous <= 0:
            continue
        priced.append(
            {
                "symbol": stock["symbol"],
                "name": stock["name"],
                "industry": stock["industry"],
                "last": last,
                "change_pct": change / previous * 100,
            }
        )
    priced.sort(key=lambda member: member["change_pct"], reverse=True)
    changes = [member["change_pct"] for member in priced]
    return {
        "count": len(stocks),
        "priced": len(priced),
        "advancing": sum(change > 0 for change in changes),
        "declining": sum(change < 0 for change in changes),
        "unchanged": sum(change == 0 for change in changes),
        "average_change": sum(changes) / len(changes) if changes else None,
        "members": priced,
    }


def build_details(
    item: dict,
    row: dict | None,
    bars: pd.DataFrame,
    stocks: list[dict],
    quotes: dict[str, dict] | None,
    is_volatility: bool,
) -> dict:
    row = row or {}
    last = row.get("last")
    if last is None and not bars.empty:
        last = float(bars["close"].iloc[-1])
    change = row.get("change")
    return {
        "name": item["name"],
        "label": item["label"],
        "last": last,
        "change": change,
        "change_pct": row.get("change_pct"),
        "previous_close": None if last is None or change is None else last - change,
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "range_position": row.get("range_position"),
        "vs_nifty": row.get("vs_nifty"),
        "returns": row.get("returns") or {},
        "quote_day": row.get("quote_day"),
        "five_days": [] if bars.empty else _five_days(bars),
        "stats": _stats(bars, last, is_volatility),
        "has_constituents": not is_volatility,
        "constituents": _members(stocks, quotes) if stocks else None,
    }

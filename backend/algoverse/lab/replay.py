"""Replay a draft strategy over recent history, for the builder's chart.

The draft is backtested on one underlying over the last few weeks or months, exactly as a
lab test would run it. Each trade comes back with its legs and fills, and with the open
position's value at every 5-minute bar (minute strategies) or close (daily ones), so the chart
can show a position being opened, carried and closed one candle at a time.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd

from algoverse.backtest.config import load_backtest_config
from algoverse.backtest.dataview import History
from algoverse.lab.evaluate import run_tasks
from algoverse.lab.spec import StrategySpec

IST = "Asia/Kolkata"
IST_SECONDS = 19_800
# Longest replays, in sessions. A daily strategy over a year runs in seconds.
MAX_SESSIONS = {"day": 250, "intraday": 125}


class ReplayError(ValueError):
    """The replay cannot run. The message says why."""


def wall_seconds(moment: datetime | pd.Timestamp | str) -> int:
    """IST wall-clock time as epoch seconds, the time axis the chart uses."""
    stamp = pd.Timestamp(moment)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(IST)
    return int(stamp.tz_convert("UTC").timestamp()) + IST_SECONDS


def window(history: History, intraday: bool, sessions: int, warmup: int) -> tuple[date, date]:
    """The last `sessions` sessions that can be replayed, after the warm-up sessions."""
    days = [d for d in history.sessions if not intraday or history.has_intraday(d)]
    usable = days[warmup:]
    if not usable:
        raise ReplayError("Not enough stored history to replay yet.")
    chosen = usable[-sessions:]
    return chosen[0], chosen[-1]


class _Sessions:
    """Each session's option prices, read once however many trades need them."""

    def __init__(self, history: History, intraday: bool):
        self.history = history
        self.intraday = intraday
        self._cache: dict[date, pd.DataFrame] = {}

    def prices(self, day: date, symbol: str) -> pd.DataFrame:
        if day not in self._cache:
            self._cache[day] = self._load(day)
        frame = self._cache[day]
        return frame[frame["symbol"] == symbol] if not frame.empty else frame

    def _load(self, day: date) -> pd.DataFrame:
        columns = ["symbol", "time", "expiry", "strike", "option_type", "close"]
        if self.intraday:
            bars = self.history.intraday(day)
            if bars.empty:
                return pd.DataFrame(columns=columns)
            # A bar's close is known at its end, so its value belongs to the next bar's start.
            times = pd.to_datetime(bars["t"] + 5 * 60_000, unit="ms", utc=True)
            seconds = (times - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)
            frame = bars.assign(time=seconds + IST_SECONDS)
        else:
            rows = self.history.day(day)
            frame = rows[rows["instrument"] == "OPT"].assign(
                time=wall_seconds(
                    pd.Timestamp(day).tz_localize(IST) + pd.Timedelta(hours=15, minutes=30)
                )
            )
        frame = frame.assign(expiry=pd.to_datetime(frame["expiry"]).dt.date.astype(str))
        return frame[columns]


def position_path(sessions: _Sessions, trade: dict) -> list[dict]:
    """The open position's value before costs, from entry to exit."""
    legs = [leg | {"expiry": str(leg["expiry"])} for leg in trade["legs"]]
    keys = {(leg["expiry"], float(leg["strike"]), leg["option_type"]) for leg in legs}
    opened = pd.Timestamp(trade["opened_at"])
    closed = pd.Timestamp(trade["closed_at"])
    days = [d for d in sessions.history.sessions if opened.date() <= d <= closed.date()]
    frames = []
    for day in days:
        prices = sessions.prices(day, trade["symbol"])
        if prices.empty:
            continue
        mask = [
            (e, float(k), o) in keys
            for e, k, o in zip(
                prices["expiry"], prices["strike"], prices["option_type"], strict=True
            )
        ]
        frames.append(prices[mask])
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return []
    prices = pd.concat(frames, ignore_index=True)
    start, end = wall_seconds(opened), wall_seconds(closed)
    prices = prices[(prices["time"] > start) & (prices["time"] < end)]
    if prices.empty:
        return []
    table = prices.pivot_table(
        index="time", columns=["expiry", "strike", "option_type"], values="close", aggfunc="last"
    ).ffill()
    points = []
    for time, row in table.iterrows():
        value, complete = 0.0, True
        for leg in legs:
            price = row.get((leg["expiry"], float(leg["strike"]), leg["option_type"]))
            if price is None or pd.isna(price):
                complete = False
                break
            sign = 1 if leg["side"] == "buy" else -1
            value += sign * leg["units"] * (float(price) - float(leg["entry_price"]))
        if complete:
            points.append({"time": int(time), "pnl": round(value, 2)})
    return points


def package(history: History, trades: list[dict], symbol: str, intraday: bool) -> list[dict]:
    """Backtest trades on one underlying, in the shape the chart's replay reads."""
    sessions = _Sessions(history, intraday)
    found = []
    for trade in trades:
        if trade["symbol"] != symbol:
            continue
        found.append(
            {
                "symbol": trade["symbol"],
                "opened_at": trade["opened_at"],
                "closed_at": trade["closed_at"],
                "open_time": wall_seconds(trade["opened_at"]),
                "close_time": wall_seconds(trade["closed_at"]),
                "lots": trade["lots"],
                "exit_reason": trade["exit_reason"],
                "net_pnl": trade["net_pnl"],
                "costs": trade["costs"],
                "planned_loss": trade["planned_loss"],
                "target_pnl": trade["target_pnl"],
                "legs": [
                    {
                        "side": leg["side"],
                        "option_type": leg["option_type"],
                        "strike": leg["strike"],
                        "expiry": str(leg["expiry"]),
                        "units": leg["units"],
                        "entry_price": leg["entry_price"],
                        "exit_price": leg["exit_price"],
                    }
                    for leg in trade["legs"]
                ],
                "path": position_path(sessions, trade),
            }
        )
    return sorted(found, key=lambda trade: trade["open_time"])


def _summary(trades: list[dict]) -> dict:
    pnls = [trade["net_pnl"] for trade in trades]
    return {
        "trades": len(pnls),
        "net_pnl": sum(pnls) if pnls else None,
        "win_rate": sum(1 for p in pnls if p > 0) / len(pnls) if pnls else None,
        "expectancy": sum(pnls) / len(pnls) if pnls else None,
        "max_drawdown": None,
    }


def replay(
    data_dir: Path,
    config_dir: Path,
    draft: StrategySpec,
    symbol: str,
    sessions: int,
    workers: int,
) -> dict:
    """Backtest an unsaved draft over the last `sessions` sessions, for the builder."""
    config = load_backtest_config(config_dir)
    if symbol not in config.symbols:
        raise ReplayError(
            f"Replay needs option history, kept for {', '.join(config.symbols)} only."
        )
    intraday = draft.decide_every != "day"
    limit = MAX_SESSIONS["intraday" if intraday else "day"]
    history = History(data_dir)
    start, end = window(history, intraday, min(sessions, limit), config.warmup_sessions)
    spec = draft.model_copy(update={"id": "replay_draft", "symbols": [symbol]})
    task = (str(data_dir), str(config_dir), spec.model_dump_json(), start, end, 1.0)
    result = run_tasks([task], workers)[0]
    trades = package(history, result["trades"], symbol, intraday)
    return {
        "symbol": symbol,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "intraday": intraday,
        "decide_every": draft.decide_every,
        "trades": trades,
        "stats": _summary(trades),
        "signals": result["signals"],
        "blocked": result["blocked"],
    }


WINDOWS = {"train": ("train_start", "train_end"), "test": ("test_start", "test_end")}


def stored_replay(
    data_dir: Path, spec: StrategySpec, result: dict, window_name: str, symbol: str
) -> dict:
    """A saved strategy's backtest, replayed from its stored trades without running again."""
    detail = result.get("detail") or {}
    parts = ["train", "test"] if window_name == "both" else [window_name]
    if any(part not in WINDOWS or part not in detail for part in parts):
        raise ReplayError("This backtest has no stored trades for that period. Test it again.")
    windows = result.get("windows") or {}
    start = windows.get(WINDOWS[parts[0]][0])
    end = windows.get(WINDOWS[parts[-1]][1])
    intraday = spec.decide_every != "day"
    raw = [trade for part in parts for trade in detail[part].get("trades", [])]
    trades = package(History(data_dir), raw, symbol, intraday)
    blocked: dict[str, int] = {}
    for part in parts:
        for reason, count in (result.get(part) or {}).get("blocked", {}).items():
            blocked[reason] = blocked.get(reason, 0) + count
    return {
        "symbol": symbol,
        "start": str(start),
        "end": str(end),
        "intraday": intraday,
        "decide_every": spec.decide_every,
        "trades": trades,
        "stats": _summary(trades),
        "signals": sum((result.get(part) or {}).get("signals", 0) or 0 for part in parts),
        "blocked": dict(sorted(blocked.items(), key=lambda item: -item[1])[:5]),
    }

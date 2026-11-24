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
from algoverse.lab.spec import ExitRules, StrategySpec

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
            # A leg closed on its own stop or target is worth its exit from then on.
            if leg.get("closed_at") and time >= wall_seconds(leg["closed_at"]):
                price = leg["exit_price"]
            if price is None or pd.isna(price):
                complete = False
                break
            sign = 1 if leg["side"] == "buy" else -1
            value += sign * leg["units"] * (float(price) - float(leg["entry_price"]))
        if complete:
            points.append({"time": int(time), "pnl": round(value, 2)})
    return points


def _exit_levels(trade: dict, exits: ExitRules | None) -> dict:
    """The trade's stop, trail and move-to-cost levels in rupees. Backtests from before these
    were recorded have only the target, so the rest is worked out from the strategy's exit
    settings: amounts in premium are fractions of the same basis the target used."""
    if "stop_pnl" in trade:
        keys = ("stop_pnl", "trail_pnl", "breakeven_pnl", "trail_step_pnl")
        return {key: trade.get(key) for key in keys}
    if exits is None:
        return {"stop_pnl": None, "trail_pnl": None, "breakeven_pnl": None, "trail_step_pnl": None}
    units = trade["legs"][0]["units"] if trade["legs"] else 0
    lot_units = units / max(trade["lots"], 1)
    basis = trade["target_pnl"] / exits.target_frac if exits.target_unit == "premium" else None

    def rupees(value: float | None, unit: str) -> float | None:
        if value is None:
            return None
        if unit == "rupees":
            return value * trade["lots"]
        if unit == "points":
            return value * lot_units * trade["lots"]
        return None if basis is None else value * basis

    stop = rupees(exits.stop_frac, exits.stop_unit)
    return {
        "stop_pnl": None if stop is None else -stop,
        "trail_pnl": rupees(exits.trail, exits.trail_unit),
        "breakeven_pnl": rupees(exits.breakeven_at, exits.breakeven_unit),
        "trail_step_pnl": rupees(exits.trail_step, exits.trail_unit),
    }


def package(
    history: History,
    trades: list[dict],
    symbol: str,
    intraday: bool,
    exits: ExitRules | None = None,
) -> list[dict]:
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
                **_exit_levels(trade, exits),
                "legs": [
                    {
                        "side": leg["side"],
                        "option_type": leg["option_type"],
                        "strike": leg["strike"],
                        "expiry": str(leg["expiry"]),
                        "units": leg["units"],
                        "entry_price": leg["entry_price"],
                        "exit_price": leg["exit_price"],
                        "stop_price": leg.get("stop_price"),
                        "target_price": leg.get("target_price"),
                        "entry_delta": leg.get("entry_delta"),
                        "exit_reason": leg.get("exit_reason"),
                        "close_time": wall_seconds(leg["closed_at"])
                        if leg.get("closed_at")
                        else None,
                    }
                    for leg in trade["legs"]
                ],
                "case": trade.get("case", 0),
                "decide_time": wall_seconds(trade["decided_at"])
                if trade.get("decided_at")
                else None,
                # None for backtests from before the reasons were recorded.
                "why": trade.get("why"),
                "entry_spot": trade.get("entry_spot"),
                "path": position_path(sessions, trade),
            }
        )
    return sorted(found, key=lambda trade: trade["open_time"])


def case_names(spec: StrategySpec) -> list[dict]:
    """The strategy's ways in, in the order trades number them: the first is the main rules."""
    return [{"name": "Entry rules", "bias": spec.bias}] + [
        {"name": case.name, "bias": case.bias} for case in spec.cases
    ]


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
    trades = package(history, result["trades"], symbol, intraday, draft.exits)
    return {
        "symbol": symbol,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "intraday": intraday,
        "decide_every": draft.decide_every,
        "trades": trades,
        "cases": case_names(draft),
        "stats": _summary(trades),
        "signals": result["signals"],
        "blocked": result["blocked"],
    }


# Longest stretch of one contract's candles the side chart loads, in sessions.
MAX_OPTION_SESSIONS = 30


def option_candles(
    data_dir: Path,
    symbol: str,
    expiry: date,
    strike: float,
    option_type: str,
    start: date,
    end: date,
) -> dict:
    """One option contract's candles from `start` to `end`: 5-minute bars where they were
    imported, otherwise the session's daily candle. Times are IST wall-clock seconds at each
    candle's start, like the index chart's."""
    if end < start:
        raise ReplayError("The end is before the start.")
    history = History(data_dir)
    sessions = [d for d in history.sessions if start <= d <= end][:MAX_OPTION_SESSIONS]
    frames, interval = [], "5m"
    for day in sessions:
        bars = history.intraday(day)
        if not bars.empty:
            bars = bars[
                (bars["symbol"] == symbol)
                & (pd.to_datetime(bars["expiry"]).dt.date == expiry)
                & (bars["strike"].astype(float) == float(strike))
                & (bars["option_type"] == option_type)
            ]
            if not bars.empty:
                frames.append(bars.assign(time=bars["t"] // 1000 + IST_SECONDS))
                continue
        rows = history.day(day)
        rows = rows[
            (rows["instrument"] == "OPT")
            & (rows["symbol"] == symbol)
            & (pd.to_datetime(rows["expiry"]).dt.date == expiry)
            & (rows["strike"].astype(float) == float(strike))
            & (rows["option_type"] == option_type)
        ]
        if not rows.empty:
            interval = "1d" if not frames else interval
            frames.append(rows.assign(time=wall_seconds(pd.Timestamp(day).tz_localize(IST))))
    empty = {"time": [], "open": [], "high": [], "low": [], "close": []}
    if not frames:
        return {"interval": interval, **empty}
    found = pd.concat(frames, ignore_index=True).sort_values("time")
    found = found.drop_duplicates("time", keep="last")
    return {
        "interval": interval,
        "time": [int(t) for t in found["time"]],
        **{
            column: [round(float(v), 2) for v in found[column]]
            for column in ("open", "high", "low", "close")
        },
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
    trades = package(History(data_dir), raw, symbol, intraday, spec.exits)
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
        "cases": case_names(spec),
        "stats": _summary(trades),
        "signals": sum((result.get(part) or {}).get("signals", 0) or 0 for part in parts),
        "blocked": dict(sorted(blocked.items(), key=lambda item: -item[1])[:5]),
    }

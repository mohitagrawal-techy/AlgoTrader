"""Market regime labels for every session, and how a strategy's trades split across them.

Each session gets three labels, all worked out from candles up to the previous session's
close, so a label is what a trader could have known before the session opened:

trend      strong_bull   ADX 25 or more, 20-day average above the 50-day, close above both
           strong_bear   ADX 25 or more, 20-day average below the 50-day, close below both
           sideways      ADX under 20
           mild_trend    anything in between
volatility high_vol      20-day realised volatility in the top 30% of the past year
           low_vol       in the bottom 30%
           normal_vol    in between
move       breakout      the close went past the highest high or lowest low of the 20
                         sessions before it
           reversal      the first close across the 20-day average after 10 or more
                         sessions on the other side
           no_signal     neither
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

from algoverse.analytics.regime import adx, ema, realised_vol

STRONG_ADX = 25.0
SIDEWAYS_ADX = 20.0
VOL_YEAR = 252
VOL_MIN_HISTORY = 60
HIGH_VOL_RANK = 0.7
LOW_VOL_RANK = 0.3
BREAKOUT_LOOKBACK = 20
REVERSAL_RUN = 10

LABELS = {
    "strong_bull": ("trend", "Strong bull"),
    "strong_bear": ("trend", "Strong bear"),
    "sideways": ("trend", "Sideways"),
    "mild_trend": ("trend", "Mild trend"),
    "high_vol": ("volatility", "High volatility"),
    "low_vol": ("volatility", "Low volatility"),
    "normal_vol": ("volatility", "Normal volatility"),
    "breakout": ("move", "Breakout"),
    "reversal": ("move", "Reversal"),
    "no_signal": ("move", "No breakout or reversal"),
}
GROUPS = ("trend", "volatility", "move")
# Labels that describe the market; the in-between ones are reported but never "suited".
NAMED = ("strong_bull", "strong_bear", "sideways", "high_vol", "low_vol", "breakout", "reversal")


def _rank(series: pd.Series) -> pd.Series:
    """Where each value sits within the year before it and itself, 0 to 1."""

    def place(window: np.ndarray) -> float:
        last = window[-1]
        past = window[~np.isnan(window)]
        if len(past) < VOL_MIN_HISTORY or np.isnan(last):
            return np.nan
        return float((past < last).mean())

    return series.rolling(VOL_YEAR, min_periods=VOL_MIN_HISTORY).apply(place, raw=True)


def label_sessions(bars: pd.DataFrame) -> pd.DataFrame:
    """One row per session (index: ISO date) with trend, volatility and move labels.

    `bars` are daily candles with ts, open, high, low and close. Sessions without enough
    history for a label get None for it."""
    if bars.empty:
        return pd.DataFrame(columns=list(GROUPS))
    frame = bars.sort_values("ts").reset_index(drop=True)
    close, high, low = frame["close"], frame["high"], frame["low"]
    fast, slow, strength = ema(close, 20), ema(close, 50), adx(frame)
    vol_rank = _rank(realised_vol(close))
    prior_high = high.shift(1).rolling(BREAKOUT_LOOKBACK).max()
    prior_low = low.shift(1).rolling(BREAKOUT_LOOKBACK).min()
    side = np.sign(close - fast)
    # Sessions in a row the close has spent on the same side of the 20-day average.
    run = side.groupby((side != side.shift()).cumsum()).cumcount() + 1

    trend = pd.Series(None, index=frame.index, dtype=object)
    known = strength.notna() & slow.notna()
    trend[known] = "mild_trend"
    trend[known & (strength < SIDEWAYS_ADX)] = "sideways"
    strong = known & (strength >= STRONG_ADX)
    trend[strong & (fast > slow) & (close > fast)] = "strong_bull"
    trend[strong & (fast < slow) & (close < fast)] = "strong_bear"

    vol = pd.Series(None, index=frame.index, dtype=object)
    vol[vol_rank.notna()] = "normal_vol"
    vol[vol_rank >= HIGH_VOL_RANK] = "high_vol"
    vol[vol_rank <= LOW_VOL_RANK] = "low_vol"

    move = pd.Series(None, index=frame.index, dtype=object)
    move[prior_high.notna() & fast.notna()] = "no_signal"
    crossed = (side != side.shift(1)) & (run.shift(1) >= REVERSAL_RUN) & (side != 0)
    move[crossed & fast.notna()] = "reversal"
    move[(close > prior_high) | (close < prior_low)] = "breakout"

    labels = pd.DataFrame({"trend": trend, "volatility": vol, "move": move})
    # A session is labelled with what was known at the previous close.
    labels = labels.shift(1)
    labels.index = pd.to_datetime(frame["ts"]).dt.date.astype(str)
    return labels


def _summary(pnls: list[float]) -> dict:
    wins = [p for p in pnls if p > 0]
    return {
        "trades": len(pnls),
        "net_pnl": float(sum(pnls)),
        "win_rate": len(wins) / len(pnls) if pnls else None,
        "expectancy": float(np.mean(pnls)) if pnls else None,
    }


def regime_breakdown(
    trades: list[dict],
    labels_by_symbol: dict[str, pd.DataFrame],
    sessions: list[str],
    min_trades: int,
) -> dict:
    """Trades grouped by the regime of the session each was opened in.

    `sessions` are the days tested, to show how often each regime came up. A named regime
    "suits" the strategy when it had at least `min_trades` trades there and made money on
    average; it is one to "avoid" when it had as many and lost on average."""
    by_label: dict[str, list[float]] = defaultdict(list)
    unlabelled = 0
    for trade in trades:
        frame = labels_by_symbol.get(trade.get("symbol", ""))
        day = str(trade.get("opened", ""))[:10]
        if frame is None or day not in frame.index:
            unlabelled += 1
            continue
        row = frame.loc[day]
        found = False
        for group in GROUPS:
            label = row[group]
            if isinstance(label, str):
                by_label[label].append(float(trade.get("net_pnl") or 0.0))
                found = True
        if not found:
            unlabelled += 1
    # How often each regime came up, on the first symbol traded (or the first available).
    frame = next(iter(labels_by_symbol.values()), None)
    mix: dict[str, int] = defaultdict(int)
    if frame is not None:
        present = frame.loc[frame.index.isin(sessions)]
        for group in GROUPS:
            for label, count in present[group].value_counts().items():
                mix[label] += int(count)
    rows = []
    for key, (group, label) in LABELS.items():
        summary = _summary(by_label.get(key, []))
        rows.append(
            {"key": key, "group": group, "label": label, "sessions": mix.get(key, 0)} | summary
        )
    enough = [
        row
        for row in rows
        if row["key"] in NAMED and row["trades"] >= min_trades and row["expectancy"] is not None
    ]
    return {
        "rows": rows,
        "suits": [row["key"] for row in enough if row["expectancy"] > 0],
        "avoid": [row["key"] for row in enough if row["expectancy"] < 0],
        "unlabelled_trades": unlabelled,
        "min_trades": min_trades,
    }

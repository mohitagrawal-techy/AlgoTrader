"""Market studies for the research page. Each takes candles and returns plain figures.

These describe the past; none of them is a trading signal by itself. Every study states the
sessions it covers, so a figure from a short sample is not mistaken for a long one.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252
BUCKET_MINUTES = 15
SESSION_OPEN_MIN = 9 * 60 + 15
SESSION_CLOSE_MIN = 15 * 60 + 30
GAP_BUCKETS = (
    (-math.inf, -1.0, "Down more than 1%"),
    (-1.0, -0.5, "Down 0.5% to 1%"),
    (-0.5, -0.2, "Down 0.2% to 0.5%"),
    (-0.2, 0.2, "Within 0.2%"),
    (0.2, 0.5, "Up 0.2% to 0.5%"),
    (0.5, 1.0, "Up 0.5% to 1%"),
    (1.0, math.inf, "Up more than 1%"),
)


def _local(frame: pd.DataFrame) -> pd.Series:
    """Timestamps on the Indian wall clock, without a zone."""
    ts = pd.to_datetime(frame["ts"])
    if ts.dt.tz is not None:
        ts = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return ts


def _days(frame: pd.DataFrame) -> pd.Series:
    return _local(frame).dt.normalize()


def _wall_minutes(frame: pd.DataFrame) -> np.ndarray:
    ts = _local(frame)
    return (ts.dt.hour * 60 + ts.dt.minute).to_numpy()


def daily_closes(frame: pd.DataFrame) -> pd.Series:
    """Close by session date, oldest first."""
    if frame.empty:
        return pd.Series(dtype=float)
    series = pd.Series(frame["close"].to_numpy(float), index=_days(frame).to_numpy())
    return series[~series.index.duplicated(keep="last")].sort_index()


# ---- Time of day ----------------------------------------------------------------------------


def time_of_day(minutes: pd.DataFrame) -> dict:
    """Return per 15-minute slot of the session: average move, how often up, typical size.

    Moves are close-to-close within each slot, in percent. The first slot starts at the
    open, so the overnight gap is left out."""
    if minutes.empty:
        return {"rows": [], "sessions": 0, "start": None, "end": None}
    frame = minutes.sort_values("ts")
    wall = _wall_minutes(frame)
    inside = (wall >= SESSION_OPEN_MIN) & (wall < SESSION_CLOSE_MIN)
    frame = frame[inside].assign(
        day=_days(frame[inside]).to_numpy(),
        slot=(wall[inside] - SESSION_OPEN_MIN) // BUCKET_MINUTES,
    )
    first = frame.groupby(["day", "slot"])["open"].first()
    last = frame.groupby(["day", "slot"])["close"].last()
    moves = ((last / first - 1) * 100).rename("move").reset_index()
    rows = []
    for slot, group in moves.groupby("slot"):
        start = SESSION_OPEN_MIN + int(slot) * BUCKET_MINUTES
        values = group["move"].to_numpy(float)
        rows.append(
            {
                "slot": f"{start // 60:02d}:{start % 60:02d}",
                "mean_pct": float(values.mean()),
                "up_share": float((values > 0).mean()),
                "abs_pct": float(np.abs(values).mean()),
                "sessions": len(values),
            }
        )
    days = sorted(moves["day"].unique())
    return {
        "rows": rows,
        "sessions": len(days),
        "start": str(pd.Timestamp(days[0]).date()),
        "end": str(pd.Timestamp(days[-1]).date()),
    }


# ---- Implied against realised volatility ----------------------------------------------------


def iv_vs_rv(closes: pd.Series, implied: pd.Series, window: int = 21) -> dict:
    """Implied volatility against the volatility that followed it.

    `implied` is annualised in percent (India VIX) or as a decimal (ATM IV); decimals are
    scaled to percent. Realised volatility over the next `window` sessions is known only in
    hindsight; the premium is implied minus that realised figure."""
    if closes.empty or implied.empty:
        return {"points": [], "sessions": 0}
    implied = implied.astype(float)
    if implied.median() < 3:
        implied = implied * 100
    returns = np.log(closes / closes.shift(1))
    trailing = returns.rolling(window).std() * math.sqrt(TRADING_DAYS) * 100
    following = trailing.shift(-window)
    frame = pd.DataFrame({"implied": implied, "trailing": trailing, "following": following}).dropna(
        subset=["implied", "trailing"]
    )
    known = frame.dropna(subset=["following"])
    premium = known["implied"] - known["following"]
    monthly = frame.groupby(pd.to_datetime(frame.index).to_period("M")).mean()
    return {
        "points": [
            {
                "month": str(month),
                "implied": float(row.implied),
                "trailing": float(row.trailing),
                "following": None if pd.isna(row.following) else float(row.following),
            }
            for month, row in monthly.iterrows()
        ],
        "sessions": len(frame),
        "start": str(pd.Timestamp(frame.index[0]).date()) if len(frame) else None,
        "end": str(pd.Timestamp(frame.index[-1]).date()) if len(frame) else None,
        "mean_premium": float(premium.mean()) if len(premium) else None,
        "premium_positive_share": float((premium > 0).mean()) if len(premium) else None,
        "latest": None
        if frame.empty
        else {
            "implied": float(frame["implied"].iloc[-1]),
            "trailing": float(frame["trailing"].iloc[-1]),
        },
        "window": window,
    }


# ---- Correlations ---------------------------------------------------------------------------


def correlations(closes: dict[str, pd.Series], sessions: int = TRADING_DAYS) -> dict:
    """Correlation of daily returns over the last `sessions` sessions they share."""
    returns = pd.DataFrame(
        {name: np.log(series / series.shift(1)) for name, series in closes.items() if len(series)}
    ).dropna()
    returns = returns.iloc[-sessions:]
    if len(returns) < 20:
        return {"names": [], "matrix": [], "sessions": len(returns)}
    matrix = returns.corr()
    return {
        "names": list(matrix.columns),
        "matrix": [[float(v) for v in row] for row in matrix.to_numpy()],
        "sessions": len(returns),
        "start": str(pd.Timestamp(returns.index[0]).date()),
        "end": str(pd.Timestamp(returns.index[-1]).date()),
    }


def rolling_correlation(a: pd.Series, b: pd.Series, window: int = 60) -> list[dict]:
    """Correlation of two return series over a rolling window, one point a week."""
    returns = pd.DataFrame({"a": np.log(a / a.shift(1)), "b": np.log(b / b.shift(1))}).dropna()
    rolling = returns["a"].rolling(window).corr(returns["b"]).dropna()
    weekly = rolling.groupby(pd.to_datetime(rolling.index).to_period("W")).last()
    return [{"week": str(p.start_time.date()), "corr": float(v)} for p, v in weekly.items()]


# ---- Gaps -----------------------------------------------------------------------------------


def gaps(daily: pd.DataFrame, minutes: pd.DataFrame | None = None) -> dict:
    """How often opening gaps filled during the session, and how often they held.

    A gap up fills when the day's low reaches the previous close; it holds when the day
    closes above the previous close. With one-minute candles, the typical time to fill is
    measured too."""
    if len(daily) < 2:
        return {"rows": [], "sessions": 0}
    frame = daily.sort_values("ts").reset_index(drop=True)
    frame = frame.assign(day=_days(frame).to_numpy(), previous=frame["close"].shift(1)).dropna(
        subset=["previous"]
    )
    gap = (frame["open"] / frame["previous"] - 1) * 100
    up = gap > 0
    filled = np.where(up, frame["low"] <= frame["previous"], frame["high"] >= frame["previous"])
    held = np.where(up, frame["close"] > frame["previous"], frame["close"] < frame["previous"])
    fill_minutes = _fill_minutes(frame, minutes) if minutes is not None else {}
    rows = []
    for low, high, label in GAP_BUCKETS:
        inside = (gap > low) & (gap <= high)
        if not inside.any():
            rows.append({"label": label, "sessions": 0})
            continue
        times = [fill_minutes[d] for d in frame.loc[inside, "day"] if d in fill_minutes]
        rows.append(
            {
                "label": label,
                "sessions": int(inside.sum()),
                "filled_share": float(filled[inside].mean()),
                "held_share": float(held[inside].mean()),
                "mean_gap_pct": float(gap[inside].mean()),
                "median_fill_minutes": float(np.median(times)) if times else None,
            }
        )
    return {
        "rows": rows,
        "sessions": len(frame),
        "start": str(pd.Timestamp(frame["day"].iloc[0]).date()),
        "end": str(pd.Timestamp(frame["day"].iloc[-1]).date()),
    }


def _fill_minutes(daily: pd.DataFrame, minutes: pd.DataFrame) -> dict:
    """Minutes after the open until each filled gap first reached the previous close."""
    if minutes is None or minutes.empty:
        return {}
    frame = minutes.sort_values("ts")
    frame = frame.assign(day=_days(frame).to_numpy(), wall=_wall_minutes(frame))
    previous = dict(zip(daily["day"], daily["previous"], strict=True))
    opening = dict(zip(daily["day"], daily["open"], strict=True))
    found = {}
    for day, group in frame.groupby("day"):
        if day not in previous:
            continue
        level, start = previous[day], opening[day]
        if start > level:
            touched = group[group["low"] <= level]
        elif start < level:
            touched = group[group["high"] >= level]
        else:
            continue
        if len(touched):
            found[day] = float(touched["wall"].iloc[0] - SESSION_OPEN_MIN)
    return found

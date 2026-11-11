"""The underlying index through one day, with the intraday indicators F&O traders watch.

Everything is computed from data already saved: the live feed's index, futures and India VIX
ticks (one point a minute), or, where the feed did not run, the spot price recorded with each
5-minute chain snapshot. Indicators use only the current and earlier points of the day.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from algoverse.analytics.levels import classic_pivots
from algoverse.analytics.regime import ema, rsi
from algoverse.data.options import chains_root
from algoverse.market.strike import IST, TICKS, _close_ms, _open_ms, chart_days

VIX_SYMBOL = "INDIAVIX"
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
BASIS_MINUTES = 30
RSI_MINUTES = 5
# Classic floor pivots drawn on the chart.
PIVOTS = {"P", "R1", "S1", "R2", "S2"}
# The opening range is the first 15 minutes of the session.
OPENING_RANGE = time(9, 30)


def _in_session(frame: pd.DataFrame, day: str) -> pd.DataFrame:
    """Rows from the 09:15 open to the 15:30 close; outside it prices repeat a close."""
    keep = (frame["t"] >= _open_ms(day)) & (frame["t"] <= _close_ms(day))
    return frame[keep].reset_index(drop=True)


def _ticks_glob(data_dir: Path, day: str) -> str | None:
    folder = Path(data_dir) / TICKS / f"date={day}"
    return str(folder / "*.parquet") if any(folder.glob("*.parquet")) else None


def _minute_ticks(source: str, where: str, params: list, columns: str) -> pd.DataFrame:
    query = f"""
        SELECT epoch_ms(time_bucket(INTERVAL 1 MINUTE, received_at)) AS t, {columns}
        FROM read_parquet(?, union_by_name = true)
        WHERE {where}
        GROUP BY t
        ORDER BY t
    """
    with duckdb.connect() as connection:
        return connection.execute(query, [source, *params]).df()


def _spot_from_ticks(source: str, symbol: str) -> pd.DataFrame:
    return _minute_ticks(
        source,
        "instrument = 'INDEX' AND symbol = ? AND ltp > 0",
        [symbol],
        "arg_max(ltp, received_at) AS spot",
    )


def _spot_from_snapshots(data_dir: Path, symbol: str, day: str) -> pd.DataFrame:
    files = list(chains_root(data_dir).glob(f"segment=*/underlying={symbol}/date={day}/*.parquet"))
    if not files:
        return pd.DataFrame(columns=["t", "spot"])
    query = """
        SELECT epoch_ms(ts) AS t, max(spot) AS spot
        FROM read_parquet(?, union_by_name = true)
        WHERE spot > 0
        GROUP BY t
        ORDER BY t
    """
    with duckdb.connect() as connection:
        return connection.execute(query, [[str(f) for f in files]]).df()


def _futures_vwap(source: str, symbol: str) -> pd.DataFrame:
    """The nearest future's VWAP (Upstox's average traded price) and its last price, by minute."""
    query = """
        SELECT expiry FROM read_parquet(?, union_by_name = true)
        WHERE instrument = 'FUT' AND symbol = ? AND atp > 0
        ORDER BY expiry LIMIT 1
    """
    with duckdb.connect() as connection:
        row = connection.execute(query, [source, symbol]).fetchone()
    if row is None:
        return pd.DataFrame(columns=["t", "fut_vwap", "fut_ltp"])
    return _minute_ticks(
        source,
        "instrument = 'FUT' AND symbol = ? AND expiry = ? AND atp > 0",
        [symbol, row[0]],
        "arg_max(atp, received_at) AS fut_vwap, arg_max(ltp, received_at) AS fut_ltp",
    )


def _pcr(data_dir: Path, symbol: str, day: str, expiry: str | None) -> list[dict]:
    """Put-call ratio of open interest for the expiry, at each snapshot."""
    if not expiry:
        return []
    files = list(chains_root(data_dir).glob(f"segment=*/underlying={symbol}/date={day}/*.parquet"))
    if not files:
        return []
    query = """
        SELECT epoch_ms(ts) AS t,
               sum(oi) FILTER (WHERE option_type = 'PE') AS puts,
               sum(oi) FILTER (WHERE option_type = 'CE') AS calls
        FROM read_parquet(?, union_by_name = true)
        WHERE expiry = ?
        GROUP BY t
        HAVING calls > 0
        ORDER BY t
    """
    with duckdb.connect() as connection:
        rows = connection.execute(
            query, [[str(f) for f in files], date.fromisoformat(expiry)]
        ).fetchall()
    opens, closes = _open_ms(day), _close_ms(day)
    return [
        {"t": int(t), "pcr": float(puts or 0) / float(calls)}
        for t, puts, calls in rows
        if opens <= t <= closes
    ]


def previous_session(
    data_dir: Path,
    symbol: str,
    day: str,
    daily: Callable[[str], pd.DataFrame],
) -> dict | None:
    """High, low and close of the last trading session before `day`.

    Daily candles are used when they reach that session; otherwise the session's own saved
    spot prices stand in, since the daily history is backfilled later. A saved day whose spot
    never moved, such as a holiday on which the collector still ran, is not a session.
    """
    session = None
    for earlier in (d for d in chart_days(data_dir, symbol) if d < day):
        spot = _session_spot(data_dir, symbol, earlier)
        if not spot.empty and spot["spot"].max() > spot["spot"].min():
            session = (earlier, spot)
            break
    candles = daily(symbol)
    if candles is not None and not candles.empty:
        times = pd.to_datetime(candles["ts"])
        if getattr(times.dt, "tz", None) is not None:
            times = times.dt.tz_convert(IST)
        candles = candles.assign(day=times.dt.strftime("%Y-%m-%d"))
        candles = candles[candles["day"] < day]
        if not candles.empty and (session is None or candles["day"].iloc[-1] >= session[0]):
            last = candles.iloc[-1]
            return {
                "day": last["day"],
                "high": float(last["high"]),
                "low": float(last["low"]),
                "close": float(last["close"]),
            }
    if session is None:
        return None
    earlier, spot = session
    return {
        "day": earlier,
        "high": float(spot["spot"].max()),
        "low": float(spot["spot"].min()),
        "close": float(spot["spot"].iloc[-1]),
    }


def _session_spot(data_dir: Path, symbol: str, day: str) -> pd.DataFrame:
    source = _ticks_glob(data_dir, day)
    spot = _spot_from_ticks(source, symbol) if source else pd.DataFrame(columns=["t", "spot"])
    if len(spot) < 2:
        spot = _spot_from_snapshots(data_dir, symbol, day)
    return _in_session(spot, day)


def _block_rsi(frame: pd.DataFrame) -> pd.Series:
    """RSI on 5-minute closes, shown on every point.

    A point inside an unfinished block shows the RSI of the last finished block, and the last
    point of a block shows that block's own value, so nothing looks ahead.
    """
    size = RSI_MINUTES * 60_000
    block = frame["t"] // size
    closes = frame.groupby(block)["spot"].last()
    values = rsi(closes, RSI_PERIOD)
    is_last = block.ne(block.shift(-1))
    own = block.map(values)
    earlier = block.map(values.shift(1))
    return own.where(is_last, earlier)


def _clean(value) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def underlying_view(
    data_dir: Path,
    symbol: str,
    daily: Callable[[str], pd.DataFrame],
    day: str | None = None,
    expiry: str | None = None,
) -> dict:
    if not symbol.isalnum():
        raise ValueError("Unknown underlying")
    if expiry:
        date.fromisoformat(expiry)
    days = chart_days(data_dir, symbol)
    chosen = day if day in days else (days[0] if days else None)
    base = {
        "symbol": symbol,
        "day": chosen,
        "days": days,
        "source": None,
        "interval_minutes": None,
        "points": [],
        "levels": {},
        "previous": None,
        "pcr": [],
        "vix": [],
    }
    if chosen is None:
        return base
    source = _ticks_glob(data_dir, chosen)
    spot = _spot_from_ticks(source, symbol) if source else pd.DataFrame(columns=["t", "spot"])
    from_ticks = len(spot) >= 2
    if not from_ticks:
        spot = _spot_from_snapshots(data_dir, symbol, chosen)
    spot = _in_session(spot, chosen)
    if spot.empty:
        return base | {"pcr": _pcr(data_dir, symbol, chosen, expiry)}

    frame = spot.copy()
    frame["vwap"] = np.nan
    if from_ticks:
        futures = _futures_vwap(source, symbol)
        if not futures.empty:
            frame = frame.drop(columns="vwap").merge(futures, on="t", how="left")
            frame[["fut_vwap", "fut_ltp"]] = frame[["fut_vwap", "fut_ltp"]].ffill()
            # Futures trade at a premium to spot, so the VWAP is moved down by that premium to
            # sit on the spot chart. The premium is the median of the last 30 minutes, so a
            # stale or jumping index print near the close does not drag the line with it.
            premium = (frame["fut_ltp"] - frame["spot"]).rolling(BASIS_MINUTES, min_periods=1)
            frame["vwap"] = frame["fut_vwap"] - premium.median()
    frame["ema_fast"] = ema(frame["spot"], EMA_FAST)
    frame["ema_slow"] = ema(frame["spot"], EMA_SLOW)
    frame["rsi"] = _block_rsi(frame)

    points = [
        {
            "t": int(row.t),
            "spot": _clean(row.spot),
            "vwap": _clean(row.vwap),
            "ema_fast": _clean(row.ema_fast),
            "ema_slow": _clean(row.ema_slow),
            "rsi": _clean(row.rsi),
        }
        for row in frame.itertuples()
    ]

    range_end = int(datetime.combine(date.fromisoformat(chosen), OPENING_RANGE, IST).timestamp())
    first_minutes = frame[frame["t"] < range_end * 1000]["spot"]
    levels: dict[str, float] = {
        "day_high": float(frame["spot"].max()),
        "day_low": float(frame["spot"].min()),
        "day_open": float(frame["spot"].iloc[0]),
    }
    # The opening range is only final once the first 15 minutes are over.
    if not first_minutes.empty and frame["t"].iloc[-1] >= range_end * 1000:
        levels["or_high"] = float(first_minutes.max())
        levels["or_low"] = float(first_minutes.min())
    previous = previous_session(data_dir, symbol, chosen, daily)
    if previous:
        levels |= {"prev_high": previous["high"], "prev_low": previous["low"]}
        levels["prev_close"] = previous["close"]
        pivots = classic_pivots(previous["high"], previous["low"], previous["close"])
        levels |= {f"pivot_{k.lower()}": v for k, v in pivots.items() if k in PIVOTS}

    vix = []
    if from_ticks:
        vix_frame = _in_session(_spot_from_ticks(source, VIX_SYMBOL), chosen)
        vix = [
            {"t": int(t), "vix": float(v)}
            for t, v in zip(vix_frame["t"], vix_frame["spot"], strict=True)
        ]
    return base | {
        "source": "ticks" if from_ticks else "snapshots",
        "interval_minutes": 1 if from_ticks else 5,
        "points": points,
        "levels": levels,
        "previous": previous,
        "pcr": _pcr(data_dir, symbol, chosen, expiry),
        "vix": vix,
    }

"""One option contract through one day, for the strike chart.

Live ticks give one point a minute. Contracts the feed did not cover (or days before it ran)
fall back to the 5-minute chain snapshots, which hold every strike.
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from algoverse.data.options import chains_root

TICKS = "ticks"
IST = ZoneInfo("Asia/Kolkata")
# Before the open the feed and the chain repeat the previous close, which is not a price of
# the day, so the chart starts at the normal NSE derivatives open.
SESSION_OPEN = time(9, 15)


def _open_ms(day: str) -> int:
    return int(datetime.combine(date.fromisoformat(day), SESSION_OPEN, IST).timestamp() * 1000)


def chart_days(data_dir: Path, symbol: str) -> list[str]:
    """Days with saved chains for the underlying, newest first."""
    days = {
        folder.name.split("=", 1)[1]
        for folder in chains_root(data_dir).glob(f"segment=*/underlying={symbol}/date=*")
        if any(folder.glob("*.parquet"))
    }
    return sorted(days, reverse=True)


SNAPSHOT_COLUMNS = ["ltp", "oi", "broker_iv", "volume", "bid", "ask"]


def _snapshots(
    folder: Path, expiry: date, strike: float, option_type: str
) -> tuple[list[float], pd.DataFrame]:
    """Every strike listed for the expiry that day, and the chosen contract's snapshots."""
    if not any(folder.glob("*.parquet")):
        return [], pd.DataFrame()
    source = "read_parquet(?, union_by_name = true)"
    with duckdb.connect() as connection:
        strikes = connection.execute(
            f"SELECT DISTINCT strike FROM {source} WHERE expiry = ? ORDER BY strike",
            [str(folder / "*.parquet"), expiry],
        ).fetchall()
        # epoch_ms keeps time zones out of the conversion; one row per snapshot time.
        frame = connection.execute(
            f"""
            SELECT epoch_ms(ts) AS t, any_value(instrument_key) AS instrument_key,
                   {", ".join(f"any_value({c}) AS {c}" for c in SNAPSHOT_COLUMNS)}
            FROM {source}
            WHERE expiry = ? AND strike = ? AND option_type = ?
            GROUP BY t
            ORDER BY t
            """,
            [str(folder / "*.parquet"), expiry, strike, option_type],
        ).df()
    return [float(row[0]) for row in strikes], frame


def _number(value) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def _snapshot_points(frame: pd.DataFrame) -> list[dict]:
    points = []
    for row in frame.itertuples():
        iv = _number(row.broker_iv)
        points.append(
            {
                "t": int(row.t),
                # A zero price means no trade yet, not a price of zero.
                "ltp": _number(row.ltp) or None,
                "oi": _number(row.oi),
                "iv": iv / 100 if iv else None,
                "volume": _number(row.volume),
                "bid": _number(row.bid) or None,
                "ask": _number(row.ask) or None,
            }
        )
    return points


def _tick_points(data_dir: Path, day: str, instrument_key: str) -> list[dict]:
    folder = Path(data_dir) / TICKS / f"date={day}"
    if not any(folder.glob("*.parquet")):
        return []
    # Aggregates are unaffected if a merge briefly leaves a tick in two files.
    query = """
        SELECT epoch_ms(time_bucket(INTERVAL 1 MINUTE, received_at)) AS t,
               arg_max(ltp, received_at) FILTER (WHERE ltp > 0) AS ltp,
               arg_max(oi, received_at) FILTER (WHERE oi > 0) AS oi,
               arg_max(iv, received_at) FILTER (WHERE iv > 0) AS iv,
               max(vtt) AS volume,
               arg_max(bid_1, received_at) FILTER (WHERE bid_1 > 0) AS bid,
               arg_max(ask_1, received_at) FILTER (WHERE ask_1 > 0) AS ask
        FROM read_parquet(?, union_by_name = true)
        WHERE instrument_key = ?
        GROUP BY t
        ORDER BY t
    """
    with duckdb.connect() as connection:
        rows = connection.execute(query, [str(folder / "*.parquet"), instrument_key]).fetchall()
    names = ["t", "ltp", "oi", "iv", "volume", "bid", "ask"]
    return [
        {name: (int(v) if name == "t" else _number(v)) for name, v in zip(names, row, strict=True)}
        for row in rows
    ]


def strike_history(
    data_dir: Path,
    symbol: str,
    expiry: str,
    strike: float,
    option_type: str,
    day: str | None = None,
) -> dict:
    option_type = option_type.upper()
    if option_type not in ("CE", "PE"):
        raise ValueError("Option type must be CE or PE")
    if not symbol.isalnum():
        raise ValueError("Unknown underlying")
    days = chart_days(data_dir, symbol)
    chosen = day if day in days else (days[0] if days else None)
    base = {
        "symbol": symbol,
        "expiry": expiry,
        "strike": strike,
        "option_type": option_type,
        "day": chosen,
        "days": days,
        "instrument_key": None,
        "source": None,
        "points": [],
        "strikes": [],
    }
    if chosen is None:
        return base
    expiry_date = date.fromisoformat(expiry)
    strikes: list[float] = []
    snapshots = pd.DataFrame()
    for folder in chains_root(data_dir).glob(f"segment=*/underlying={symbol}/date={chosen}"):
        strikes, snapshots = _snapshots(folder, expiry_date, float(strike), option_type)
        if strikes:
            break
    base["strikes"] = strikes
    if snapshots.empty:
        return base
    key = str(snapshots["instrument_key"].iloc[-1])
    opens = _open_ms(chosen)
    ticks = [p for p in _tick_points(data_dir, chosen, key) if p["t"] >= opens]
    if len(ticks) >= 2:
        return base | {"instrument_key": key, "source": "ticks", "points": ticks}
    points = [p for p in _snapshot_points(snapshots) if p["t"] >= opens]
    return base | {"instrument_key": key, "source": "snapshots", "points": points}

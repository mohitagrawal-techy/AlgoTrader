"""The whole-market snapshot behind the Overview page."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from algoverse.analytics.regime import ema
from algoverse.brokers.base import AuthExpiredError, MarketDataError
from algoverse.dashboard import MARKET_FILE, clean, read_published
from algoverse.data.candles import MINUTE
from algoverse.data.stream import live_dir, read_status
from algoverse.market.index_details import build_details, load_constituents
from algoverse.market.institutions import empty_institutions, fetch_institutions
from algoverse.market.sectors import load_sector_list

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
OPEN_TTL_SECONDS = 20.0
CLOSED_TTL_SECONDS = 300.0
INTRADAY_MINUTES = 5
FETCH_MINUTES = 1
INTRADAY_OPEN_TTL_SECONDS = 10.0
LIVE_PRICE_MAX_AGE_SECONDS = 15.0
INTRADAY_CLOSED_TTL_SECONDS = 600.0
SESSION_LOOKBACK_DAYS = 10
INSTITUTIONS_TTL_SECONDS = 1800.0
HISTORY_DAYS = 520
CHART_SESSIONS = 252
DISTRIBUTION_EDGES = [-float("inf"), -5, -3, -2, -1, 0, 1, 2, 3, 5, float("inf")]
DISTRIBUTION_LABELS = [
    "Below -5%",
    "-5 to -3%",
    "-3 to -2%",
    "-2 to -1%",
    "-1 to 0%",
    "0 to 1%",
    "1 to 2%",
    "2 to 3%",
    "3 to 5%",
    "Above 5%",
]
PERIODS = {"1W": 5, "1M": 21, "3M": 63, "1Y": 252}
MOVERS = 10
HEATMAP_STOCKS = 220
LIVE, CLOSED, SESSION_EXPIRED, UNAVAILABLE = "live", "closed", "session_expired", "unavailable"


def load_market_config(config_dir: Path) -> dict:
    with (config_dir / "market.yaml").open() as f:
        return yaml.safe_load(f)


def index_key(name: str) -> str:
    return f"NSE_INDEX|{name}"


def _ist_day(milliseconds) -> date | None:
    try:
        return datetime.fromtimestamp(int(milliseconds) / 1000, IST).date()
    except (TypeError, ValueError):
        return None


def _change_pct(last: float, change: float) -> float | None:
    previous = last - change
    return None if previous <= 0 else change / previous * 100


def period_returns(closes: pd.DataFrame, last: float, quote_day: date | None) -> dict:
    """Return from N completed sessions before the latest quote to the latest price."""
    if closes.empty or quote_day is None:
        return {}
    days = pd.to_datetime(closes["ts"]).dt.date
    prior = closes.loc[days < quote_day, "close"].to_numpy(float)
    return {
        label: (last / prior[-sessions] - 1) * 100
        for label, sessions in PERIODS.items()
        if len(prior) >= sessions and prior[-sessions] > 0
    }


def build_index_row(
    item: dict, quote: dict | None, closes: pd.DataFrame, nifty_change: float | None
) -> dict | None:
    if quote is None or quote.get("last_price") is None or quote.get("net_change") is None:
        return None
    last, change = float(quote["last_price"]), float(quote["net_change"])
    ohlc = quote.get("ohlc") or {}
    high, low = ohlc.get("high"), ohlc.get("low")
    change_pct = _change_pct(last, change)
    span = (high - low) if high is not None and low is not None else None
    return {
        "name": item["name"],
        "label": item["label"],
        "last": last,
        "change": change,
        "change_pct": change_pct,
        "open": ohlc.get("open"),
        "high": high,
        "low": low,
        "range_position": (last - low) / span * 100 if span else None,
        "vs_nifty": None
        if change_pct is None or nifty_change is None
        else change_pct - nifty_change,
        "returns": period_returns(closes, last, _ist_day(quote.get("last_trade_time"))),
        "quote_day": _ist_day(quote.get("last_trade_time")),
    }


def stock_frame(stocks: list[dict], quotes: dict[str, dict]) -> pd.DataFrame:
    """One row per stock that has traded in the latest session."""
    rows = []
    for stock in stocks:
        quote = quotes.get(f"NSE_EQ|{stock['isin']}")
        if not quote or not quote.get("volume") or quote.get("net_change") is None:
            continue
        last = float(quote["last_price"])
        change_pct = _change_pct(last, float(quote["net_change"]))
        if change_pct is None:
            continue
        rows.append(
            {
                "symbol": stock["symbol"],
                "name": stock["name"],
                "industry": stock["industry"],
                "last": last,
                "change_pct": change_pct,
                "value": float(quote["volume"]) * float(quote.get("average_price") or last),
            }
        )
    return pd.DataFrame(rows)


def breadth(frame: pd.DataFrame) -> dict | None:
    if frame.empty:
        return None
    change = frame["change_pct"]
    return {
        "stocks": len(frame),
        "advancing": int((change > 0).sum()),
        "declining": int((change < 0).sum()),
        "unchanged": int((change == 0).sum()),
        "average_change": float(change.mean()),
        "up_over_2": int((change >= 2).sum()),
        "down_over_2": int((change <= -2).sum()),
        "value": float(frame["value"].sum()),
    }


def _pick(row: pd.Series) -> dict:
    return {"symbol": row["symbol"], "change_pct": float(row["change_pct"])}


def industries(frame: pd.DataFrame) -> list[dict]:
    if frame.empty:
        return []
    rows = []
    for industry, group in frame.groupby("industry"):
        change = group["change_pct"]
        rows.append(
            {
                "industry": industry,
                "stocks": len(group),
                "advancing": int((change > 0).sum()),
                "declining": int((change < 0).sum()),
                "average_change": float(change.mean()),
                "value": float(group["value"].sum()),
                "best": _pick(group.loc[change.idxmax()]),
                "worst": _pick(group.loc[change.idxmin()]),
            }
        )
    return sorted(rows, key=lambda row: -row["average_change"])


def _records(frame: pd.DataFrame) -> list[dict]:
    columns = ["symbol", "name", "industry", "last", "change_pct", "value"]
    return frame[columns].to_dict("records")


def movers(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"gainers": [], "losers": [], "active": []}
    return {
        "gainers": _records(frame.nlargest(MOVERS, "change_pct")),
        "losers": _records(frame.nsmallest(MOVERS, "change_pct")),
        "active": _records(frame.nlargest(MOVERS, "value")),
    }


def heatmap(frame: pd.DataFrame) -> list[dict]:
    """Most-traded stocks grouped by industry, largest group first."""
    if frame.empty:
        return []
    top = frame.nlargest(HEATMAP_STOCKS, "value")
    groups = [
        {
            "industry": industry,
            "value": float(group["value"].sum()),
            "stocks": group.sort_values("value", ascending=False)[
                ["symbol", "name", "change_pct", "value"]
            ].to_dict("records"),
        }
        for industry, group in top.groupby("industry")
    ]
    return sorted(groups, key=lambda group: -group["value"])


def distribution(frame: pd.DataFrame) -> list[dict]:
    """How many stocks fell into each band of today's change."""
    if frame.empty:
        return []
    bands = pd.cut(frame["change_pct"], DISTRIBUTION_EDGES, labels=DISTRIBUTION_LABELS, right=False)
    counts = bands.value_counts().reindex(DISTRIBUTION_LABELS, fill_value=0)
    return [{"label": label, "count": int(count)} for label, count in counts.items()]


def merge_live_bar(frame: pd.DataFrame, row: dict | None) -> pd.DataFrame:
    """Daily bars, with the latest quote as the newest bar. `frame` has date and OHLC columns."""
    if (
        not row
        or not row.get("quote_day")
        or None in (row.get("open"), row.get("high"), row.get("low"))
    ):
        return frame
    day = row["quote_day"]
    day = date.fromisoformat(day) if isinstance(day, str) else day
    bar = {
        "date": day,
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["last"],
    }
    earlier = frame[frame["date"] < day]
    return pd.concat([earlier, pd.DataFrame([bar])], ignore_index=True)


def candle_rows(frame: pd.DataFrame, sessions: int = CHART_SESSIONS) -> list[dict]:
    """Newest bars with moving averages computed on the full history first."""
    if frame.empty:
        return []
    chart = frame.assign(ema20=ema(frame["close"], 20), ema50=ema(frame["close"], 50))
    return chart.tail(sessions).to_dict("records")


def options_snapshot(data_dir: Path) -> list[dict]:
    published = read_published(data_dir, MARKET_FILE) or {}
    rows = []
    for symbol, market in published.get("symbols", {}).items():
        expiry = (market.get("expiries") or [{}])[0]
        rows.append(
            {
                "symbol": symbol,
                "trend": market.get("trend"),
                "iv_rank": market.get("iv_rank"),
                "atm_iv": expiry.get("atm_iv"),
                "pcr": expiry.get("pcr"),
                "max_pain": expiry.get("max_pain"),
                "expected_move": expiry.get("expected_move"),
                "expiry": expiry.get("expiry"),
            }
        )
    return rows


def resample_candles(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Combine one-minute candles into longer ones, labelled by their start time."""
    ordered = frame.assign(ts=pd.to_datetime(frame["ts"])).sort_values("ts")
    bucket = ordered["ts"].dt.floor(f"{minutes}min")
    grouped = ordered.groupby(bucket)
    return pd.DataFrame(
        {
            "ts": grouped["ts"].first().index,
            "open": grouped["open"].first().to_numpy(float),
            "high": grouped["high"].max().to_numpy(float),
            "low": grouped["low"].min().to_numpy(float),
            "close": grouped["close"].last().to_numpy(float),
        }
    ).reset_index(drop=True)


def with_live_price(
    bars: pd.DataFrame, price: float | None, now: datetime, minutes: int
) -> pd.DataFrame:
    """Fold the latest price into the forming candle, starting it if Upstox has not yet."""
    if price is None or bars.empty:
        return bars
    bucket = pd.Timestamp(now.astimezone(IST)).floor(f"{minutes}min")
    last = bars.iloc[-1]
    if pd.Timestamp(last["ts"]) == bucket:
        updated = bars.copy()
        index = updated.index[-1]
        updated.loc[index, "close"] = price
        updated.loc[index, "high"] = max(float(last["high"]), price)
        updated.loc[index, "low"] = min(float(last["low"]), price)
        return updated
    if pd.Timestamp(last["ts"]) > bucket:
        return bars
    forming = {
        "ts": bucket,
        "open": float(last["close"]),
        "high": max(float(last["close"]), price),
        "low": min(float(last["close"]), price),
        "close": price,
    }
    return pd.concat([bars, pd.DataFrame([forming])], ignore_index=True)


class OverviewService:
    """Fetches at most once per cache period, however many browser tabs are polling."""

    def __init__(
        self,
        data_dir: Path,
        config_dir: Path,
        market,
        store,
        calendar,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.data_dir = Path(data_dir)
        self.config_dir = config_dir
        self.market = market
        self.store = store
        self.calendar = calendar
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: dict | None = None
        self._cached_at = float("-inf")
        self._history_day: date | None = None
        self._institutions_lock = threading.Lock()
        self._institutions: dict | None = None
        self._institutions_at = float("-inf")
        self._intraday_lock = threading.Lock()
        self._intraday: dict[tuple[str, date], tuple[float, pd.DataFrame]] = {}
        self._member_quotes_lock = threading.Lock()
        self._member_quotes: dict[str, tuple[float, dict[str, dict] | None]] = {}

    def _institutions_path(self) -> Path:
        return self.data_dir / "market" / "institutions.json"

    def _institutions_fallback(self, status: str) -> dict:
        try:
            saved = json.loads(self._institutions_path().read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return empty_institutions(status)
        return saved | {"status": status}

    def institutions(self, now: datetime) -> dict:
        """FII and DII activity. Published daily, so it is fetched at most twice an hour."""
        with self._institutions_lock:
            fresh = self._clock() - self._institutions_at < INSTITUTIONS_TTL_SECONDS
            if self._institutions is not None and fresh:
                return self._institutions
            try:
                payload = clean(fetch_institutions(self.market, now))
            except AuthExpiredError:
                return self._institutions_fallback(SESSION_EXPIRED)
            except MarketDataError as exc:
                log.warning("FII and DII fetch failed: %s", exc)
                return self._institutions_fallback(UNAVAILABLE)
            self._institutions, self._institutions_at = payload, self._clock()
            path = self._institutions_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload))
            os.replace(tmp, path)
            return payload

    def _saved_path(self) -> Path:
        return self.data_dir / "market" / "overview.json"

    def _read_saved(self) -> dict | None:
        try:
            return json.loads(self._saved_path().read_text())
        except FileNotFoundError:
            return None

    def _write_saved(self, payload: dict) -> None:
        path = self._saved_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, allow_nan=False))
        os.replace(tmp, path)

    def invalidate(self) -> None:
        with self._lock:
            self._cached_at = float("-inf")

    def _refresh_history(self, config: dict, today: date) -> None:
        """Daily closes for period returns. One slow pass a day, away from the request."""
        items = {i["name"]: i for i in [*config["broad"], *config["sectors"], config["volatility"]]}
        start = today - timedelta(days=HISTORY_DAYS)
        saved = False
        for name in items:
            try:
                candles = self.market.daily_candles(index_key(name), start, today)
            except MarketDataError as exc:
                log.warning("Index history failed for %s: %s", name, exc)
                continue
            if not candles.empty:
                self.store.upsert_candles(index_key(name), candles)
                saved = True
        if saved:
            self.invalidate()  # The next request should include the returns just loaded.

    def _maybe_refresh_history(self, config: dict, today: date) -> None:
        if self._history_day != today:
            self._history_day = today
            threading.Thread(
                target=self._refresh_history, args=(config, today), daemon=True
            ).start()

    def _build(self, now: datetime, is_open: bool) -> dict:
        config = load_market_config(self.config_dir)
        stocks = load_sector_list(self.data_dir)
        volatility = config["volatility"]
        names = {i["name"] for i in [*config["broad"], *config["sectors"], volatility]}
        keys = [index_key(name) for name in sorted(names)]
        keys += [f"NSE_EQ|{stock['isin']}" for stock in stocks]
        quotes = self.market.quotes(keys)
        self._maybe_refresh_history(config, now.date())

        def closes(name: str) -> pd.DataFrame:
            return self.store.read_candles(index_key(name))

        nifty = quotes.get(index_key("Nifty 50"))
        nifty_change = (
            _change_pct(float(nifty["last_price"]), float(nifty["net_change"])) if nifty else None
        )

        def rows(items: list[dict]) -> list[dict]:
            built = (
                build_index_row(
                    i, quotes.get(index_key(i["name"])), closes(i["name"]), nifty_change
                )
                for i in items
            )
            return [row for row in built if row is not None]

        frame = stock_frame(stocks, quotes)
        last_trades = [
            q.get("last_trade_time") for q in quotes.values() if q.get("last_trade_time")
        ]
        return {
            "status": LIVE if is_open else CLOSED,
            "fetched_at": now,
            "last_trade_at": datetime.fromtimestamp(int(max(last_trades, key=int)) / 1000, IST)
            if last_trades
            else None,
            "indices": rows(config["broad"]),
            "volatility": build_index_row(
                volatility, quotes.get(index_key(volatility["name"])), pd.DataFrame(), None
            ),
            "sectors": sorted(rows(config["sectors"]), key=lambda r: -(r["change_pct"] or 0.0)),
            "has_sector_list": bool(stocks),
            "universe_size": len(stocks),
            "breadth": breadth(frame),
            "industries": industries(frame),
            "movers": movers(frame),
            "heatmap": heatmap(frame),
            "distribution": distribution(frame),
        }

    def _fallback(self, status: str) -> dict:
        saved = self._read_saved() or {
            "fetched_at": None,
            "last_trade_at": None,
            "indices": [],
            "volatility": None,
            "sectors": [],
            "has_sector_list": bool(load_sector_list(self.data_dir)),
            "universe_size": 0,
            "breadth": None,
            "industries": [],
            "movers": {"gainers": [], "losers": [], "active": []},
            "heatmap": [],
            "distribution": [],
        }
        return saved | {"status": status}

    def snapshot(self, now: datetime) -> dict:
        is_open = self.calendar.is_open(now)
        ttl = OPEN_TTL_SECONDS if is_open else CLOSED_TTL_SECONDS
        with self._lock:
            if self._cached is not None and self._clock() - self._cached_at < ttl:
                return self._cached | {"options": options_snapshot(self.data_dir)}
            try:
                payload = clean(self._build(now, is_open))
            except AuthExpiredError:
                return clean(self._fallback(SESSION_EXPIRED)) | {
                    "options": options_snapshot(self.data_dir)
                }
            except MarketDataError as exc:
                log.warning("Overview fetch failed: %s", exc)
                return clean(self._fallback(UNAVAILABLE)) | {
                    "options": options_snapshot(self.data_dir)
                }
            self._cached, self._cached_at = payload, self._clock()
            self._write_saved(payload)
            return payload | {"options": options_snapshot(self.data_dir)}

    def _bars(self, name: str, live_rows: dict[str, dict]) -> pd.DataFrame:
        columns = ["date", "open", "high", "low", "close"]
        stored = self.store.read_candles(index_key(name))
        if stored.empty:
            return merge_live_bar(pd.DataFrame(columns=columns), None)
        frame = stored.assign(date=pd.to_datetime(stored["ts"]).dt.date)[columns]
        return merge_live_bar(frame.sort_values("date", ignore_index=True), live_rows.get(name))

    def _live_rows(self, now: datetime) -> dict[str, dict]:
        data = self.snapshot(now)
        rows = [*data["indices"], *data["sectors"]]
        if data.get("volatility"):
            rows.append(data["volatility"])
        return {row["name"]: row for row in rows}

    def history(self, name: str, now: datetime) -> dict | None:
        """Candles for one configured index, or None when the name is not configured."""
        config = load_market_config(self.config_dir)
        items = {i["name"]: i for i in [*config["broad"], *config["sectors"], config["volatility"]]}
        if name not in items:
            return None
        bars = self._bars(name, self._live_rows(now))
        return clean({"name": name, "label": items[name]["label"], "candles": candle_rows(bars)})

    def _index_items(self) -> tuple[dict[str, dict], str]:
        config = load_market_config(self.config_dir)
        volatility = config["volatility"]
        items = {i["name"]: i for i in [*config["sectors"], *config["broad"], volatility]}
        return items, volatility["name"]

    def constituent_lists(self) -> list[dict]:
        """Every index that can have a stock list, and how many stocks each list holds."""
        items, volatility = self._index_items()
        return [
            {
                "name": name,
                "label": item["label"],
                "stocks": len(load_constituents(self.data_dir, name)),
            }
            for name, item in sorted(items.items(), key=lambda pair: pair[1]["label"])
            if name != volatility
        ]

    def accepts_constituents(self, name: str) -> bool:
        items, volatility = self._index_items()
        return name in items and name != volatility

    def forget_member_quotes(self, name: str) -> None:
        with self._member_quotes_lock:
            self._member_quotes.pop(name, None)

    def _quotes_for_members(
        self, name: str, stocks: list[dict], is_open: bool
    ) -> dict[str, dict] | None:
        ttl = OPEN_TTL_SECONDS if is_open else CLOSED_TTL_SECONDS
        with self._member_quotes_lock:
            cached = self._member_quotes.get(name)
        if cached is not None and self._clock() - cached[0] < ttl:
            return cached[1]
        try:
            quotes = self.market.quotes([f"NSE_EQ|{stock['isin']}" for stock in stocks])
        except MarketDataError as exc:
            log.warning("Constituent quotes failed for %s: %s", name, exc)
            return None
        with self._member_quotes_lock:
            self._member_quotes[name] = (self._clock(), quotes)
        return quotes

    def details(self, name: str, now: datetime) -> dict | None:
        """The hover card for one index, or None when the name is not configured."""
        items, volatility = self._index_items()
        if name not in items:
            return None
        live = self._live_rows(now)
        stocks = [] if name == volatility else load_constituents(self.data_dir, name)
        quotes = (
            self._quotes_for_members(name, stocks, self.calendar.is_open(now)) if stocks else None
        )
        return clean(
            build_details(
                items[name],
                live.get(name),
                self._bars(name, live),
                stocks,
                quotes,
                name == volatility,
            )
        )

    def _sessions_back(self, now: datetime) -> list[date]:
        """Today when its session has started, then earlier trading days, newest first."""
        local = now.astimezone(IST)
        days = []
        for back in range(SESSION_LOOKBACK_DAYS):
            day = local.date() - timedelta(days=back)
            hours = self.calendar.session(day)
            if hours is None or (back == 0 and local.time() < hours[0]):
                continue
            days.append(day)
        return days

    def _saved_minutes(self, name: str, day: date) -> pd.DataFrame | None:
        """One-minute candles saved for a session, from candles/interval=1m."""
        frame = self.store.read_candles(index_key(name), MINUTE, start=day, end=day)
        return None if frame.empty else frame

    def _save_minutes(self, name: str, day: date, frame: pd.DataFrame) -> None:
        self.store.upsert_candles(index_key(name), frame, MINUTE)

    def _is_complete(self, frame: pd.DataFrame, day: date) -> bool:
        hours = self.calendar.session(day)
        if frame.empty or hours is None:
            return False
        last = pd.to_datetime(frame["ts"]).max().tz_convert(IST)
        closing = datetime.combine(day, hours[1], tzinfo=IST) - timedelta(minutes=FETCH_MINUTES)
        return last >= closing

    def _minutes(self, name: str, day: date, now: datetime) -> pd.DataFrame:
        """One-minute candles for a session, saved to disk as they arrive.

        A finished session is read from disk. If Upstox cannot be reached, the saved copy is used.
        """
        today = now.astimezone(IST).date()
        saved = self._saved_minutes(name, day)
        if day != today and saved is not None and self._is_complete(saved, day):
            return saved
        live = day == today and self.calendar.is_open(now)
        ttl = INTRADAY_OPEN_TTL_SECONDS if live else INTRADAY_CLOSED_TTL_SECONDS
        with self._intraday_lock:
            cached = self._intraday.get((name, day))
        if cached is not None and self._clock() - cached[0] < ttl:
            return cached[1]
        key = index_key(name)
        try:
            frame = (
                self.market.intraday_candles(key, FETCH_MINUTES)
                if day == today
                else self.market.minute_candles(key, day, FETCH_MINUTES)
            )
        except MarketDataError:
            if saved is not None:
                return saved
            raise
        if not frame.empty:
            self._save_minutes(name, day, frame)
        with self._intraday_lock:
            self._intraday[(name, day)] = (self._clock(), frame)
        return frame

    def _live_price(self, name: str, now: datetime) -> float | None:
        """The latest price: from the live feed when it is running, else the latest quote."""
        status = read_status(self.data_dir) or {}
        updated = status.get("updated_at")
        path = live_dir(self.data_dir) / "latest.parquet"
        fresh = (
            status.get("state") == "connected"
            and updated is not None
            and (now - datetime.fromisoformat(updated)).total_seconds()
            <= LIVE_PRICE_MAX_AGE_SECONDS
        )
        if fresh and path.exists():
            latest = pd.read_parquet(path, columns=["instrument_key", "ltp"])
            match = latest.loc[latest["instrument_key"] == index_key(name), "ltp"]
            if not match.empty and match.iloc[0] > 0:
                return float(match.iloc[0])
        row = self._live_rows(now).get(name)
        return float(row["last"]) if row and row.get("last") else None

    def _session_candles(self, name: str, now: datetime) -> tuple[date | None, pd.DataFrame, bool]:
        """Five-minute candles for the latest session with data, and whether they are live."""
        today = now.astimezone(IST).date()
        empty = pd.DataFrame(columns=["ts", "open", "high", "low", "close"])
        for day in self._sessions_back(now)[:2]:
            minutes = self._minutes(name, day, now)
            if minutes.empty:
                continue
            bars = resample_candles(minutes, INTRADAY_MINUTES)
            live = day == today and self.calendar.is_open(now)
            if live:
                bars = with_live_price(bars, self._live_price(name, now), now, INTRADAY_MINUTES)
            return day, bars, live
        return None, empty, False

    def _previous_close(self, name: str, session: date | None, now: datetime) -> float | None:
        if session is None:
            return None
        bars = self._bars(name, self._live_rows(now))
        earlier = bars[bars["date"] < session]
        return float(earlier["close"].iloc[-1]) if not earlier.empty else None

    def intraday(self, name: str, now: datetime) -> dict | None:
        """Five-minute candles for the latest session, or None for an unknown name."""
        items, _ = self._index_items()
        if name not in items:
            return None
        try:
            session, frame, live = self._session_candles(name, now)
        except AuthExpiredError:
            return clean(
                {
                    "name": name,
                    "label": items[name]["label"],
                    "status": SESSION_EXPIRED,
                    "session": None,
                    "previous_close": None,
                    "candles": [],
                }
            )
        except MarketDataError as exc:
            log.warning("Intraday candles failed for %s: %s", name, exc)
            return clean(
                {
                    "name": name,
                    "label": items[name]["label"],
                    "status": UNAVAILABLE,
                    "session": None,
                    "previous_close": None,
                    "candles": [],
                }
            )
        closes = frame["close"].astype(float)
        chart = frame.assign(
            date=pd.to_datetime(frame["ts"]).map(lambda ts: ts.isoformat()),
            ema20=ema(closes, 20),
            ema50=ema(closes, 50),
        )
        return clean(
            {
                "name": name,
                "label": items[name]["label"],
                "status": "ok",
                "session": session,
                "live": live,
                "updated_at": now,
                "previous_close": self._previous_close(name, session, now),
                "candles": chart[
                    ["date", "open", "high", "low", "close", "ema20", "ema50"]
                ].to_dict("records"),
            }
        )

    def intraday_comparison(self, now: datetime) -> dict:
        """The latest session's path for the main indices and India VIX."""
        config = load_market_config(self.config_dir)
        selected = [i for i in config["broad"] if i.get("compare", True)]
        series, sessions, status = [], set(), "ok"
        for item in [*selected, config["volatility"]]:
            try:
                session, frame, live = self._session_candles(item["name"], now)
            except AuthExpiredError:
                status = SESSION_EXPIRED
                break
            except MarketDataError as exc:
                log.warning("Intraday candles failed for %s: %s", item["name"], exc)
                status = UNAVAILABLE
                continue
            if session is None:
                continue
            sessions.add(session)
            series.append(
                {
                    "name": item["name"],
                    "label": item["label"],
                    "session": session,
                    "live": live,
                    "previous_close": self._previous_close(item["name"], session, now),
                    "times": [ts.isoformat() for ts in pd.to_datetime(frame["ts"])],
                    "closes": frame["close"].astype(float).tolist(),
                }
            )
        volatility_name = config["volatility"]["name"]
        return clean(
            {
                "status": status,
                "updated_at": now,
                "live": any(item["live"] for item in series),
                "session": max(sessions) if sessions else None,
                "series": [s for s in series if s["name"] != volatility_name],
                "volatility": next((s for s in series if s["name"] == volatility_name), None),
            }
        )

    def comparison(self, now: datetime) -> dict:
        """Closes on shared dates for the main indices, plus India VIX."""
        config = load_market_config(self.config_dir)
        live = self._live_rows(now)
        selected = [i for i in config["broad"] if i.get("compare", True)]
        closes = {
            item["label"]: self._bars(item["name"], live).set_index("date")["close"]
            for item in selected
        }
        frame = pd.DataFrame(closes).dropna().tail(CHART_SESSIONS) if closes else pd.DataFrame()
        vix = self._bars(config["volatility"]["name"], live).tail(CHART_SESSIONS)
        return clean(
            {
                "dates": list(frame.index),
                "series": [{"label": column, "closes": list(frame[column])} for column in frame],
                "volatility": {"dates": list(vix["date"]), "closes": list(vix["close"])},
            }
        )

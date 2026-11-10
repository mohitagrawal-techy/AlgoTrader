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
from algoverse.market.institutions import empty_institutions, fetch_institutions
from algoverse.market.sectors import load_sector_list

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
OPEN_TTL_SECONDS = 20.0
CLOSED_TTL_SECONDS = 300.0
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


def _series_name(name: str) -> str:
    return "index_" + name.replace(" ", "_")


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
                self.store.upsert_candles(_series_name(name), candles)
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
            return self.store.read_candles(_series_name(name))

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
        stored = self.store.read_candles(_series_name(name))
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

"""The research page: market studies from stored candles, and the ML signal study."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from algoverse.backtest.config import load_backtest_config
from algoverse.dashboard import clean
from algoverse.data.candles import MINUTE
from algoverse.data.store import SnapshotStore
from algoverse.research import ml, pairs, studies

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
VIX = "INDIAVIX"
# Sector indices for the correlation table, by instrument key. Their history starts in 2025.
SECTORS = {
    "IT": "NSE_INDEX|Nifty IT",
    "Pharma": "NSE_INDEX|Nifty Pharma",
    "Metal": "NSE_INDEX|Nifty Metal",
    "Auto": "NSE_INDEX|Nifty Auto",
    "FMCG": "NSE_INDEX|Nifty FMCG",
}
PAIRS = (("NIFTY", "BANKNIFTY"), ("BANKNIFTY", "FINNIFTY"))


class ResearchError(Exception):
    pass


class ResearchService:
    def __init__(self, data_dir: Path, config_dir: Path):
        self.data_dir = Path(data_dir)
        self.config_dir = Path(config_dir)
        self._cache: dict[tuple, dict] = {}
        self._lock = threading.Lock()
        self.job: dict | None = None

    def _store(self) -> SnapshotStore:
        return SnapshotStore(self.data_dir)

    def _daily(self, name: str) -> pd.DataFrame:
        try:
            return self._store().read_candles(name)
        except (KeyError, ValueError, FileNotFoundError):
            return pd.DataFrame()

    def symbols(self) -> list[str]:
        return list(load_backtest_config(self.config_dir).symbols)

    def studies(self, symbol: str) -> dict:
        """Every market study for one index, cached until new candles arrive."""
        if symbol not in self.symbols():
            raise ResearchError(f"Choose one of {', '.join(self.symbols())}.")
        daily = self._daily(symbol)
        if daily.empty:
            raise ResearchError(f"No daily candles for {symbol} yet.")
        marker = (symbol, str(daily["ts"].iloc[-1]), len(daily))
        if marker not in self._cache:
            self._cache = {marker: self._build(symbol, daily)}
        return self._cache[marker]

    def _build(self, symbol: str, daily: pd.DataFrame) -> dict:
        store = self._store()
        try:
            minutes = store.read_candles(symbol, MINUTE)
        except (KeyError, ValueError, FileNotFoundError):
            minutes = pd.DataFrame()
        closes = studies.daily_closes(daily)
        vix = studies.daily_closes(self._daily(VIX))
        try:
            atm = store.read_candles(f"iv_{symbol}")
        except (KeyError, ValueError, FileNotFoundError):
            atm = pd.DataFrame()
        # The daily ATM IV series, collected live; it grows by one point a session.
        atm_series = studies.daily_closes(atm) if "close" in atm else pd.Series(dtype=float)
        names = {name: studies.daily_closes(self._daily(name)) for name in self.symbols()}
        names["India VIX"] = vix
        names["SENSEX"] = studies.daily_closes(self._daily("SENSEX"))
        for label, key in SECTORS.items():
            names[label] = studies.daily_closes(self._daily(key))
        pair_rows = []
        for first, second in PAIRS:
            a, b = names.get(first), names.get(second)
            if a is not None and b is not None and len(a) and len(b):
                pair_rows.append(pairs.pair_study(a, b, (first, second)))
        others = [name for name in self.symbols() if name != symbol]
        return clean(
            {
                "symbol": symbol,
                "symbols": self.symbols(),
                "time_of_day": studies.time_of_day(minutes),
                "vix_vs_rv": studies.iv_vs_rv(closes, vix) if symbol == "NIFTY" else None,
                "atm_iv_vs_rv": studies.iv_vs_rv(closes, atm_series)
                if len(atm_series) >= 21
                else None,
                "atm_iv_sessions": len(atm_series),
                "correlations": studies.correlations(names),
                "rolling_correlation": {
                    "other": others[0],
                    "points": studies.rolling_correlation(closes, names[others[0]]),
                }
                if others
                else None,
                "gaps": studies.gaps(daily, minutes if len(minutes) else None),
                "pairs": pair_rows,
                "ml": self.ml_study(symbol),
            }
        )

    # ---- ML study --------------------------------------------------------------------------

    def _study_path(self, symbol: str) -> Path:
        return self.data_dir / "research" / "ml" / f"{symbol}_study.json"

    def ml_study(self, symbol: str) -> dict | None:
        try:
            study = json.loads(self._study_path(symbol).read_text())
        except (OSError, ValueError):
            return None
        study.pop("probabilities", None)
        return study

    def run_ml(self, symbol: str, horizon: int, now: datetime) -> dict:
        daily = self._daily(symbol)
        if daily.empty:
            raise ResearchError(f"No daily candles for {symbol} yet.")
        vix = studies.daily_closes(self._daily(VIX))
        study = ml.walk_forward(ml.features(daily, vix), horizon=horizon)
        if "error" in study:
            raise ResearchError(study["error"])
        study = clean(study | {"symbol": symbol, "run_at": now.isoformat()})
        path = self._study_path(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(study))
        ml.save_signal(self.data_dir, symbol, study)
        self._cache.clear()
        return study

    def start_ml(self, symbol: str, horizon: int, now: datetime) -> dict:
        if symbol not in self.symbols():
            raise ResearchError(f"Choose one of {', '.join(self.symbols())}.")
        with self._lock:
            if self.job and self.job["state"] == "running":
                raise ResearchError("The ML study is already running.")
            job = {
                "kind": "ml",
                "state": "running",
                "message": f"Training walk-forward models for {symbol}",
                "started_at": now.isoformat(),
                "finished_at": None,
            }
            self.job = job

        def run() -> None:
            try:
                study = self.run_ml(symbol, horizon, now)
                verdict = "adopted" if study["adopted"] else "not adopted"
                job["message"] = (
                    f"Tested {study['sessions_tested']} unseen sessions: {verdict} against "
                    f"{study['best_rule'].lower()}."
                )
                job["state"] = "done"
            except ResearchError as exc:
                job["state"], job["message"] = "failed", str(exc)
            except Exception:
                log.exception("ML study failed")
                job["state"], job["message"] = "failed", "The study failed. See the server log."
            job["finished_at"] = datetime.now(IST).isoformat()

        threading.Thread(target=run, daemon=True).start()
        return job

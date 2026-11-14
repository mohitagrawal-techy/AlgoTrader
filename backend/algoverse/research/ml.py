"""A machine-learning direction signal, judged only on sessions its models never saw.

Gradient-boosted trees (scikit-learn's histogram booster, the same algorithm family as
LightGBM) predict whether the index closes higher after `horizon` sessions, from indicators
and India VIX known at the close.

Testing is walk-forward: each block of sessions is predicted by a model trained only on
earlier sessions, with a gap of `horizon + embargo` sessions between the last training
target and the first prediction, so no target overlaps the block it is tested on. The model's
settings are fixed in advance and never tuned on results.

The model is adopted only if its out-of-sample trading result beats the best simple rule on
the same sessions by more than luck explains. Its walk-forward probabilities can then be read
by lab strategies as the `ml_prob_up` signal; each one came from a model that had not seen
that session.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import log_loss, roc_auc_score

from algoverse.analytics.regime import adx, ema, rsi
from algoverse.lab.evidence import prob_better, probabilistic_sharpe

TRADING_DAYS = 252
SEED = 7
LONG_ABOVE = 0.55
SHORT_BELOW = 0.45
FEATURE_LABELS = {
    "ret_1": "1-session return, %",
    "ret_5": "5-session return, %",
    "ret_20": "20-session return, %",
    "rv_20": "20-session realised volatility",
    "rv_ratio": "5-session over 20-session volatility",
    "rsi_14": "RSI 14",
    "adx_14": "ADX 14",
    "ema20_gap": "Close vs 20-session average, %",
    "ema50_gap": "Close vs 50-session average, %",
    "atr_pct": "14-session ATR, % of price",
    "gap_pct": "Opening gap, %",
    "range_pct": "Session range, %",
    "from_high_20": "Below 20-session high, %",
    "from_low_20": "Above 20-session low, %",
    "vix": "India VIX",
    "vix_change_5": "India VIX 5-session change, %",
    "vix_rank": "India VIX rank in the past year",
    "weekday": "Day of the week",
}


def features(bars: pd.DataFrame, vix: pd.Series | None = None) -> pd.DataFrame:
    """One row per session, from candles up to and including that session's close."""
    frame = bars.sort_values("ts").reset_index(drop=True)
    days = pd.to_datetime(frame["ts"])
    if days.dt.tz is not None:
        days = days.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    days = days.dt.normalize()
    close, high, low, open_ = frame["close"], frame["high"], frame["low"], frame["open"]
    log_return = np.log(close / close.shift(1))
    true_range = pd.concat(
        [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1
    ).max(axis=1)
    out = pd.DataFrame(
        {
            "ret_1": log_return * 100,
            "ret_5": np.log(close / close.shift(5)) * 100,
            "ret_20": np.log(close / close.shift(20)) * 100,
            "rv_20": log_return.rolling(20).std() * math.sqrt(TRADING_DAYS),
            "rv_ratio": log_return.rolling(5).std() / log_return.rolling(20).std(),
            "rsi_14": rsi(close),
            "adx_14": adx(frame),
            "ema20_gap": (close / ema(close, 20) - 1) * 100,
            "ema50_gap": (close / ema(close, 50) - 1) * 100,
            "atr_pct": true_range.ewm(alpha=1 / 14, adjust=False).mean() / close * 100,
            "gap_pct": (open_ / close.shift(1) - 1) * 100,
            "range_pct": (high - low) / close.shift(1) * 100,
            "from_high_20": (1 - close / high.rolling(20).max()) * 100,
            "from_low_20": (close / low.rolling(20).min() - 1) * 100,
            "weekday": days.dt.day_of_week.astype(float),
        }
    )
    out.index = days.to_numpy()
    if vix is not None and len(vix):
        level = vix.reindex(out.index).ffill()
        out["vix"] = level
        out["vix_change_5"] = (level / level.shift(5) - 1) * 100
        out["vix_rank"] = level.rolling(TRADING_DAYS, min_periods=60).rank(pct=True)
    out["close"] = close.to_numpy()
    return out


def _model() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_depth=3,
        learning_rate=0.05,
        max_iter=150,
        l2_regularization=1.0,
        min_samples_leaf=40,
        random_state=SEED,
    )


def _rules(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """Simple rules to beat, each a position of +1, 0 or -1 per session."""
    return {
        "Always long": pd.Series(1.0, index=frame.index),
        "Trend: above the 50-session average": np.sign(frame["ema50_gap"]).fillna(0.0),
        "Momentum: 5-session return": np.sign(frame["ret_5"]).fillna(0.0),
        "RSI: buy under 30, sell over 70": pd.Series(
            np.where(frame["rsi_14"] < 30, 1.0, np.where(frame["rsi_14"] > 70, -1.0, 0.0)),
            index=frame.index,
        ),
    }


def _trade(position: pd.Series, forward: pd.Series, cost_bps: float) -> pd.Series:
    """Return per session of holding `position` over the next session, after costs."""
    costs = position.diff().abs().fillna(position.abs()) * cost_bps / 10_000
    return position * forward - costs


def _summary(returns: pd.Series, position: pd.Series) -> dict:
    values = returns.to_numpy(float)
    deviation = values.std(ddof=1) if len(values) > 1 else 0.0
    equity = np.cumsum(values)
    active = position != 0
    return {
        "sessions": len(values),
        "total_return_pct": float(equity[-1] * 100) if len(values) else 0.0,
        "sharpe": None
        if deviation == 0
        else float(values.mean() / deviation * math.sqrt(TRADING_DAYS)),
        "psr": probabilistic_sharpe(list(values)),
        "max_drawdown_pct": float(np.max(np.maximum.accumulate(equity) - equity) * 100)
        if len(values)
        else 0.0,
        "hit_rate": float((returns[active] > 0).mean()) if active.any() else None,
        "time_in_market": float(active.mean()),
    }


def walk_forward(
    frame: pd.DataFrame,
    horizon: int = 1,
    min_train: int = 750,
    block: int = 63,
    embargo: int = 5,
    cost_bps: float = 2.0,
    min_prob_better: float = 0.9,
) -> dict:
    """Train, predict each block out of sample, and compare with the simple rules."""
    data = frame.copy()
    future = np.log(data["close"].shift(-horizon) / data["close"])
    data["target"] = (future > 0).astype(float).where(future.notna())
    data["forward_1"] = np.log(data["close"].shift(-1) / data["close"])
    names = [c for c in FEATURE_LABELS if c in data.columns]
    usable = data.dropna(subset=names)
    if len(usable) < min_train + block:
        return {
            "error": f"Needs {min_train + block} sessions with every feature; has {len(usable)}."
        }
    gap = horizon + embargo
    probabilities = pd.Series(np.nan, index=usable.index)
    folds = []
    model = None
    start = min_train
    while start < len(usable):
        end = min(start + block, len(usable))
        train = usable.iloc[: max(start - gap, 0)].dropna(subset=["target"])
        test = usable.iloc[start:end]
        model = _model().fit(train[names], train["target"])
        probabilities.iloc[start:end] = model.predict_proba(test[names])[:, 1]
        folds.append(
            {
                "train_end": str(pd.Timestamp(train.index[-1]).date()),
                "test_start": str(pd.Timestamp(test.index[0]).date()),
                "test_end": str(pd.Timestamp(test.index[-1]).date()),
                "train_sessions": len(train),
            }
        )
        start = end
    tested = usable.loc[probabilities.notna()].assign(prob=probabilities.dropna())
    scored = tested.dropna(subset=["target", "forward_1"])
    position = pd.Series(
        np.where(scored["prob"] > LONG_ABOVE, 1.0, np.where(scored["prob"] < SHORT_BELOW, -1.0, 0)),
        index=scored.index,
    )
    ml_returns = _trade(position, scored["forward_1"], cost_bps)
    rules = {
        name: (
            rule_position := rule.reindex(scored.index).fillna(0.0),
            _trade(rule_position, scored["forward_1"], cost_bps),
        )
        for name, rule in _rules(usable).items()
    }
    rule_rows = [
        {"name": name} | _summary(returns, held) for name, (held, returns) in rules.items()
    ]
    best = max(rule_rows, key=lambda row: row["sharpe"] if row["sharpe"] is not None else -99)
    best_returns = rules[best["name"]][1]
    chance = prob_better(
        {str(k): float(v) for k, v in ml_returns.items()},
        {str(k): float(v) for k, v in best_returns.items()},
    )
    ml = _summary(ml_returns, position) | {
        "accuracy": float(((scored["prob"] > 0.5) == (scored["target"] > 0.5)).mean()),
        "auc": float(roc_auc_score(scored["target"], scored["prob"]))
        if scored["target"].nunique() == 2
        else None,
        "log_loss": float(log_loss(scored["target"], scored["prob"], labels=[0, 1])),
        "base_rate": float(scored["target"].mean()),
    }
    adopted = (
        ml["sharpe"] is not None
        and best["sharpe"] is not None
        and ml["sharpe"] > best["sharpe"]
        and chance >= min_prob_better
    )
    importance = []
    last = usable.dropna(subset=["target"]).iloc[-min(len(usable), 2 * block) :]
    if model is not None and len(last) > 20 and last["target"].nunique() == 2:
        found = permutation_importance(
            model, last[names], last["target"], n_repeats=5, random_state=SEED, scoring="roc_auc"
        )
        importance = sorted(
            (
                {"feature": FEATURE_LABELS[name], "importance": float(value)}
                for name, value in zip(names, found.importances_mean, strict=True)
            ),
            key=lambda row: row["importance"],
            reverse=True,
        )
    # Today's reading, from a model trained on every session whose target is known.
    known = usable.dropna(subset=["target"])
    final = _model().fit(
        known[names].iloc[: max(len(known) - embargo, 1)],
        known["target"].iloc[: max(len(known) - embargo, 1)],
    )
    latest_day = usable.index[-1]
    latest = float(final.predict_proba(usable[names].iloc[[-1]])[:, 1][0])
    probabilities.loc[latest_day] = latest
    return {
        "horizon": horizon,
        "features": [FEATURE_LABELS[name] for name in names],
        "folds": folds,
        "sessions_tested": len(scored),
        "start": str(pd.Timestamp(scored.index[0]).date()) if len(scored) else None,
        "end": str(pd.Timestamp(scored.index[-1]).date()) if len(scored) else None,
        "ml": ml,
        "rules": rule_rows,
        "best_rule": best["name"],
        "prob_better_than_best": chance,
        "adopted": adopted,
        "importance": importance,
        "latest": {"day": str(pd.Timestamp(latest_day).date()), "prob_up": latest},
        "probabilities": {
            str(pd.Timestamp(day).date()): float(value)
            for day, value in probabilities.dropna().items()
        },
        "rules_note": f"Long above {LONG_ABOVE:.0%} chance up, short below {SHORT_BELOW:.0%}, "
        f"flat between; {cost_bps:g} bps per change of position.",
        "equity": [
            {
                "day": str(pd.Timestamp(day).date()),
                "ml": float(m * 100),
                "best_rule": float(b * 100),
            }
            for (day, m), b in zip(
                ml_returns.cumsum().iloc[::5].items(),
                best_returns.cumsum().iloc[::5].to_numpy(),
                strict=True,
            )
        ],
    }


# ---- The lab signal -------------------------------------------------------------------------

_ACTIVE: dict[str, pd.Series] = {}


def signal_path(data_dir: Path, symbol: str) -> Path:
    return Path(data_dir) / "research" / "ml" / f"{symbol}.json"


def save_signal(data_dir: Path, symbol: str, study: dict) -> None:
    path = signal_path(data_dir, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"adopted": study["adopted"], "probabilities": study["probabilities"]}
    path.write_text(json.dumps(payload))


def activate(data_dir: Path, symbols: list[str]) -> None:
    """Load saved walk-forward probabilities so `probability` can answer during replays."""
    for symbol in symbols:
        try:
            payload = json.loads(signal_path(data_dir, symbol).read_text())
        except (OSError, ValueError):
            _ACTIVE.pop(symbol, None)
            continue
        series = pd.Series(payload["probabilities"], dtype=float)
        series.index = pd.to_datetime(series.index).date
        _ACTIVE[symbol] = series.sort_index()


def probability(symbol: str, day: date, after_close: bool) -> float | None:
    """The walk-forward chance of an up move known at the decision.

    After the close, the session's own reading is known; before it, only the previous
    session's, which forecasts the move into today's close."""
    series = _ACTIVE.get(symbol)
    if series is None or series.empty:
        return None
    known = series[series.index <= day] if after_close else series[series.index < day]
    return None if known.empty else float(known.iloc[-1])

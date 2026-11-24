"""Open-interest analytics for a single expiry."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _oi_by_strike(chain: pd.DataFrame) -> pd.DataFrame:
    table = chain.pivot_table(
        index="strike", columns="option_type", values="oi", aggfunc="sum"
    ).fillna(0.0)
    return table.reindex(columns=["CE", "PE"], fill_value=0.0).sort_index()


def put_call_ratio(chain: pd.DataFrame) -> float | None:
    """Total put OI divided by total call OI."""
    table = _oi_by_strike(chain)
    calls = table["CE"].sum()
    return None if calls == 0 else float(table["PE"].sum() / calls)


def max_pain(chain: pd.DataFrame) -> float | None:
    """The settlement strike that minimises total option-holder payout."""
    table = _oi_by_strike(chain)
    if table.empty:
        return None
    strikes = table.index.to_numpy(float)
    settle = strikes[:, np.newaxis]
    call_payout = np.maximum(settle - strikes, 0.0) @ table["CE"].to_numpy()
    put_payout = np.maximum(strikes - settle, 0.0) @ table["PE"].to_numpy()
    return float(strikes[np.argmin(call_payout + put_payout)])


def oi_walls(chain: pd.DataFrame, count: int = 3) -> dict[str, list[float]]:
    """Largest OI strikes: calls as resistance candidates, puts as support candidates."""
    table = _oi_by_strike(chain)
    return {
        "resistance": [float(k) for k in table["CE"].nlargest(count).index],
        "support": [float(k) for k in table["PE"].nlargest(count).index],
    }


def classify_buildup(chain: pd.DataFrame) -> pd.Series:
    """Label each contract from its price change and OI change since the previous close."""
    price_change = chain["ltp"] - chain["close_price"]
    oi_change = chain["oi"] - chain["prev_oi"]
    labels = np.select(
        [
            (price_change > 0) & (oi_change > 0),
            (price_change < 0) & (oi_change > 0),
            (price_change > 0) & (oi_change < 0),
            (price_change < 0) & (oi_change < 0),
        ],
        ["long_buildup", "short_buildup", "short_covering", "long_unwinding"],
        default="neutral",
    )
    return pd.Series(labels, index=chain.index, name="buildup")

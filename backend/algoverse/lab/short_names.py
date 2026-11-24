"""Short, plain names for indicators and rules, for places with little room such as the tuner.

"opening range body(15) Close (15-minute)" reads as "First 15m close (15m)", and a whole rule
as "Rule 2 (5m): Low below First 15m open (15m)". The long wording in `technical.rule_text`
stays as it is, since saved results and reasons use it.
"""

from __future__ import annotations

from algoverse.lab.technical import (
    INDICATORS,
    OPS,
    PATTERNS,
    CompareRule,
    Operand,
    PatternRule,
    SignalRule,
)

FRAMES = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "1d": "1D", "1w": "1W", "1mo": "1M"}
# Indicators whose usual short name is an abbreviation followed by its settings.
ABBREVIATIONS = {
    "sma": "SMA",
    "ema": "EMA",
    "wma": "WMA",
    "hma": "HMA",
    "smma": "SMMA",
    "rsi": "RSI",
    "macd": "MACD",
    "bbands": "BB",
    "atr": "ATR",
    "atr_pct": "ATR %",
    "supertrend": "Supertrend",
    "adx": "ADX",
    "stochastic": "Stoch",
    "roc": "ROC",
    "cci": "CCI",
    "williams_r": "Williams %R",
    "donchian": "Donchian",
    "keltner": "Keltner",
    "ichimoku": "Ichimoku",
    "psar": "SAR",
    "aroon": "Aroon",
    "awesome": "AO",
    "chandelier": "Chandelier",
    "coppock": "Coppock",
    "fisher": "Fisher",
    "halftrend": "HalfTrend",
    "stoch_rsi": "Stoch RSI",
    "tii": "TII",
    "alligator": "Alligator",
    "qqe": "QQE",
    "rsi_ma": "RSI",
    "atr_ma": "ATR",
}
# Parts of an indicator, shortened; a part not listed keeps its own label.
FIELDS = {
    "upper": "upper",
    "middle": "middle",
    "lower": "lower",
    "width_pct": "width %",
    "percent_b": "%B",
    "line": "line",
    "direction": "direction",
    "signal": "signal",
    "hist": "histogram",
    "plus_di": "+DI",
    "minus_di": "-DI",
    "ma": "average",
    "rsi_ma": "smoothed",
    "trailing": "trailing",
    "span_a": "span A",
    "span_b": "span B",
    "conversion": "conversion",
    "base": "base",
    "oscillator": "oscillator",
    "long": "long stop",
    "short": "short stop",
    "sar": "",
    "macd": "",
    "adx": "",
    "atr": "",
    "rsi": "",
    "fisher": "",
    "tii": "",
    "k": "%K",
    "d": "%D",
    "trigger": "trigger",
}
PLAIN = {
    "close": "Close",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "prev_close": "Prev close",
    "prev_high": "Prev high",
    "prev_low": "Prev low",
    "body_pct": "Body %",
    "gap_pct": "Gap %",
    "change_pct": "Change %",
}


def _settings(operand: Operand) -> list[str]:
    indicator = INDICATORS[operand.name]
    return [f"{operand.params.get(p.name, p.default):g}" for p in indicator.params]


def _field(operand: Operand) -> str:
    if not operand.field:
        return ""
    label = INDICATORS[operand.name].fields.get(operand.field, operand.field)
    return FIELDS.get(operand.field, label.lower())


def short_operand(operand: Operand) -> str:
    """One side of a rule in a few words: "EMA 20", "First 15m close", "Yesterday high"."""
    if operand.kind == "value":
        return f"{operand.value:g}"
    name = operand.name or ""
    if name not in INDICATORS:
        return name
    settings = _settings(operand)
    field = _field(operand)
    if name in PLAIN:
        text = PLAIN[name]
    elif name in ("candle", "prev_candle"):
        text = f"{'Prev candle' if name == 'prev_candle' else 'Candle'} {field}".strip()
    elif name in ("opening_range", "opening_range_body"):
        text = f"First {settings[0]}m {field or 'range'}".strip()
    elif name == "session_avg":
        text = "VWAP" if operand.field in (None, "avg") else f"VWAP {field}"
    elif name in ("prior_high", "prior_low"):
        text = f"{settings[0]}-candle {'high' if name == 'prior_high' else 'low'}"
    elif name == "range_pct":
        text = f"{settings[0]}-candle range %"
    elif name == "today":
        text = f"Today {field}".strip()
    elif name == "prev_day":
        text = f"Yesterday {field}".strip()
    elif name == "candle_at":
        at = int(float(settings[0]))
        text = f"{at // 100:02d}:{at % 100:02d} candle {field}".strip()
    elif name in ("pivots", "cpr"):
        text = INDICATORS[name].fields.get(operand.field or "p", "Pivot")
    else:
        abbreviation = ABBREVIATIONS.get(name, INDICATORS[name].label)
        text = " ".join(part for part in (abbreviation, "/".join(settings), field) if part)
    if operand.bars_ago:
        text += f", {operand.bars_ago} ago"
    if operand.timeframe:
        heikin = " HA" if operand.candles == "heikin_ashi" else ""
        text += f" ({FRAMES.get(operand.timeframe, operand.timeframe)}{heikin})"
    return text


def short_rule(rule: CompareRule | PatternRule | SignalRule) -> str:
    """A rule in a line: "(5m) Low below First 15m open (15m)"."""
    if isinstance(rule, SignalRule):
        return f"{rule.name} is {'true' if rule.present else 'false'}"
    frame = FRAMES.get(rule.timeframe, rule.timeframe)
    if rule.candles == "heikin_ashi":
        frame += " HA"
    if isinstance(rule, PatternRule):
        verb = "" if rule.present else "no "
        when = "" if rule.within == 1 else f" in last {rule.within}"
        return f"({frame}) {verb}{PATTERNS[rule.pattern].label.lower()}{when}"
    return f"({frame}) {short_operand(rule.left)} {OPS[rule.op]} {short_operand(rule.right)}"

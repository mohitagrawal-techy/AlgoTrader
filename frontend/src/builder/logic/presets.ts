import type {
  CandleType,
  TechOperand,
  TechRule,
  TechTimeframe,
} from "../../api";

/** An indicator operand: its name, settings and the line it reads. */
const ind = (
  name: string,
  params?: Record<string, number>,
  field?: string,
): TechOperand => ({
  kind: "indicator",
  name,
  ...(params ? { params } : {}),
  ...(field ? { field } : {}),
});
const num = (value: number): TechOperand => ({ kind: "value", value });
const close = ind("close");

function compare(
  left: TechOperand,
  op: "above" | "below" | "crosses_above" | "crosses_below",
  right: TechOperand,
  timeframe: TechTimeframe = "1d",
  candles?: CandleType,
): TechRule {
  return {
    type: "compare",
    timeframe,
    ...(candles ? { candles } : {}),
    left,
    op,
    right,
  };
}

const pattern = (name: string): TechRule => ({
  type: "pattern",
  timeframe: "1d",
  pattern: name,
  within: 1,
  present: true,
});

const MACD = { fast: 12, slow: 26, signal: 9 };

/** Ready-made rules for common setups. They are ordinary rules and can be edited after. */
export const PRESETS: { label: string; rule: TechRule }[] = [
  {
    label: "Close above EMA 20",
    rule: compare(close, "above", ind("ema", { period: 20 })),
  },
  {
    label: "EMA 9 crosses above EMA 21",
    rule: compare(
      ind("ema", { period: 9 }),
      "crosses_above",
      ind("ema", { period: 21 }),
    ),
  },
  {
    label: "RSI 14 below 30",
    rule: compare(ind("rsi", { period: 14 }), "below", num(30)),
  },
  {
    label: "Breaks the 20-day high",
    rule: compare(close, "above", ind("prior_high", { period: 20 })),
  },
  {
    label: "Supertrend up",
    rule: compare(
      ind("supertrend", { period: 10, multiplier: 3 }, "direction"),
      "above",
      num(0),
    ),
  },
  {
    label: "MACD above signal",
    rule: compare(
      ind("macd", MACD, "macd"),
      "above",
      ind("macd", MACD, "signal"),
    ),
  },
  {
    label: "Weekly close above weekly EMA 20",
    rule: compare(close, "above", ind("ema", { period: 20 }), "1w"),
  },
  {
    label: "Narrow range, 5 days under 2%",
    rule: compare(ind("range_pct", { period: 5 }), "below", num(2)),
  },
  {
    label: "Closes above the first 15 minutes' open–close",
    rule: compare(
      close,
      "above",
      ind("opening_range_body", { minutes: 15 }, "high"),
      "15m",
    ),
  },
  {
    label: "Closes below the first 15 minutes' open–close",
    rule: compare(
      close,
      "below",
      ind("opening_range_body", { minutes: 15 }, "low"),
      "15m",
    ),
  },
  {
    label: "Breaks above the 15-minute opening range",
    rule: compare(
      close,
      "above",
      ind("opening_range", { minutes: 15 }, "high"),
      "5m",
    ),
  },
  {
    label: "Breaks below the 15-minute opening range",
    rule: compare(
      close,
      "below",
      ind("opening_range", { minutes: 15 }, "low"),
      "5m",
    ),
  },
  {
    label: "Heikin-Ashi candle green",
    rule: compare(close, "above", ind("open"), "1d", "heikin_ashi"),
  },
  {
    label: "CCI 20 above 100",
    rule: compare(ind("cci", { period: 20 }), "above", num(100)),
  },
  {
    label: "Williams %R 14 below -80",
    rule: compare(ind("williams_r", { period: 14 }), "below", num(-80)),
  },
  { label: "Bullish engulfing", rule: pattern("bullish_engulfing") },
  { label: "Bearish engulfing", rule: pattern("bearish_engulfing") },
];

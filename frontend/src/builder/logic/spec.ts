import type { ExitUnit, LabLeg, LabSpec, LegUnit } from "../../api";

export const MAX_LEGS = 4;
export const MAX_CONDITIONS = 6;
export const MAX_RATIONALE = 600;
export const MAX_CASES = 3;
export const TRENDS = ["up", "down", "range"] as const;
export const BIASES = [
  { value: "bullish", label: "Bullish" },
  { value: "neutral", label: "Neutral" },
  { value: "bearish", label: "Bearish" },
] as const;
export const DECISIONS = [
  { value: "day", label: "Once a day, at the close" },
  { value: "5m", label: "Every 5 minutes" },
  { value: "15m", label: "Every 15 minutes" },
  { value: "1h", label: "Every hour" },
] as const;

export const UNITS: { value: ExitUnit; label: string }[] = [
  { value: "premium", label: "× premium" },
  { value: "rupees", label: "₹ per lot" },
  { value: "points", label: "points" },
];
export const UNIT_RANGES: Record<ExitUnit, [number, number]> = {
  premium: [0.05, 5],
  rupees: [1, 1_000_000],
  points: [0.05, 10_000],
};
/** A sensible starting amount when the unit changes. */
export const UNIT_DEFAULTS: Record<ExitUnit, number> = {
  premium: 0.5,
  rupees: 2000,
  points: 20,
};
export const LEG_UNIT_RANGES: Record<LegUnit, [number, number]> = {
  pct: [1, 500],
  points: [0.05, 10_000],
};
export const LEG_UNIT_DEFAULTS: Record<LegUnit, number> = {
  pct: 25,
  points: 20,
};

export type Budget = NonNullable<LabSpec["budget"]>;
export const DEFAULT_BUDGET: Budget = {
  capital: 100000,
  risk_per_trade_pct: 5,
  max_open: 5,
  sizing: "budget",
  deploy_pct: 100,
};

export const NEW_LEG: LabLeg = {
  action: "buy",
  option_type: "CE",
  ratio: 1,
  select: "delta",
  value: 0.5,
  expiry: 0,
};

export function blankSpec(): LabSpec {
  return {
    name: "",
    rationale: "",
    bias: "neutral",
    symbols: [],
    legs: [{ ...NEW_LEG }],
    entry: {
      trend: [],
      iv_rank_min: null,
      iv_rank_max: null,
      dte_min: 5,
      dte_max: 30,
      conditions: [],
      rules: [],
    },
    exits: { target_frac: 0.5, stop_frac: 0.5, exit_dte: 1 },
    budget: { ...DEFAULT_BUDGET },
    // Every strategy starts with the candle itself, as the indicator most rules read.
    indicators: [
      { name: "candle", params: {}, timeframe: "1d", candles: "normal" },
    ],
  };
}

/** "09:30:00" from the server, "09:30" from an input: both shown as "09:30". */
export function hhmm(value: string | null | undefined): string {
  return value ? value.slice(0, 5) : "";
}

export function amountText(
  value: number | null | undefined,
  unit: ExitUnit | undefined,
): string {
  if (value === null || value === undefined) return "–";
  if (unit === "rupees") return `₹${value.toLocaleString("en-IN")}/lot`;
  if (unit === "points") return `${value} pts`;
  return `${value}× premium`;
}

export function legText(leg: LabLeg): string {
  const strike =
    leg.select === "delta"
      ? `delta ${leg.value}`
      : leg.select === "premium"
        ? `premium near ₹${leg.value}`
        : leg.value
          ? `ATM${leg.value > 0 ? "+" : ""}${leg.value}`
          : "ATM";
  const later = leg.expiry ? `, expiry +${leg.expiry}` : "";
  const level = (
    amount: number | null | undefined,
    unit: LegUnit | undefined,
  ) => (unit === "points" ? `${amount} pts` : `${amount}%`);
  const own = [
    leg.stop != null ? `SL ${level(leg.stop, leg.stop_unit)}` : "",
    leg.target != null ? `TP ${level(leg.target, leg.target_unit)}` : "",
  ].filter(Boolean);
  const levels = own.length ? ` (${own.join(", ")})` : "";
  return `${leg.ratio} ${leg.option_type === "CE" ? "Call" : "Put"} ${strike}${later}${levels}`;
}

export type Complexity = "Simple" | "Moderate" | "Complex";

export function complexity(spec: LabSpec): Complexity {
  const { entry } = spec;
  const hasIv = entry.iv_rank_min !== null || entry.iv_rank_max !== null;
  const filters =
    Number(entry.trend.length > 0) +
    Number(hasIv) +
    (entry.conditions ?? []).length +
    (entry.rules ?? []).length +
    Number((entry.any_rules ?? []).length > 0);
  if (spec.legs.length >= 3 || filters > 2) return "Complex";
  if (spec.legs.length === 2 || filters > 1) return "Moderate";
  return "Simple";
}

export function sellsMoreThanBuys(spec: LabSpec): boolean {
  const net = { CE: 0, PE: 0 };
  for (const leg of spec.legs)
    net[leg.option_type] += (leg.action === "buy" ? 1 : -1) * leg.ratio;
  return net.CE < 0 || net.PE < 0;
}

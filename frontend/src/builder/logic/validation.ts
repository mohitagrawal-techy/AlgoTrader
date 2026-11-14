import type { ExitUnit, LabLeg, LabSpec } from "../../api";
import { DEFAULT_BUDGET, LEG_UNIT_RANGES, UNIT_RANGES } from "./spec";

/** A problem message for each field id that needs fixing. */
export type Problems = Record<string, string>;

export function inRange(
  value: number | null,
  low: number,
  high: number,
): boolean {
  return (
    value !== null && Number.isFinite(value) && value >= low && value <= high
  );
}

const upTo = (low: number, high: number) =>
  `${low} to ${high.toLocaleString("en-IN")}`;

export function legProblems(
  legs: LabLeg[],
  prefix: string,
  problems: Problems,
): void {
  legs.forEach((leg, index) => {
    const id = `${prefix}-${index}`;
    if (!inRange(leg.ratio, 1, 4) || !Number.isInteger(leg.ratio)) {
      problems[`${id}-ratio`] = "Lots: use a whole number from 1 to 4.";
    }
    if (leg.select === "delta" && !inRange(leg.value, 0.05, 0.95)) {
      problems[`${id}-value`] = "Strike: use a delta from 0.05 to 0.95.";
    }
    if (
      leg.select === "atm_offset" &&
      (!inRange(leg.value, -20, 20) || !Number.isInteger(leg.value))
    ) {
      problems[`${id}-value`] =
        "Strike: use a whole number of strikes from -20 to 20.";
    }
    if (leg.select === "premium" && !inRange(leg.value, 0.5, 5000)) {
      problems[`${id}-value`] = "Strike: use a premium of ₹0.5 to ₹5,000.";
    }
    for (const field of ["stop", "target"] as const) {
      const amount = leg[field];
      if (amount === null || amount === undefined) continue;
      const [low, high] = LEG_UNIT_RANGES[leg[`${field}_unit`] ?? "pct"];
      if (!inRange(amount, low, high)) {
        const name = field === "stop" ? "Stop loss" : "Take profit";
        problems[`${id}-${field}`] =
          `${name}: use ${upTo(low, high)}, or leave empty.`;
      }
    }
  });
  const seen = new Set<string>();
  legs.forEach((leg, index) => {
    const key = [leg.option_type, leg.select, leg.value, leg.expiry].join("|");
    if (seen.has(key))
      problems[`${prefix}-${index}-value`] =
        "Strike: another leg already uses it.";
    seen.add(key);
  });
}

function entryProblems(
  spec: LabSpec,
  problems: Problems,
  sameDay: boolean,
): void {
  const { entry } = spec;
  for (const key of ["iv_rank_min", "iv_rank_max"] as const) {
    if (entry[key] !== null && !inRange(entry[key], 0, 100))
      problems[key] = "Use 0 to 100, or leave empty.";
  }
  if (
    entry.iv_rank_min !== null &&
    entry.iv_rank_max !== null &&
    entry.iv_rank_min > entry.iv_rank_max
  ) {
    problems.iv_rank_max = "Must not be below the minimum.";
  }
  // Expiry day itself (0) is allowed for intraday strategies that square off the same day.
  if (!inRange(entry.dte_min, sameDay ? 0 : 1, 45)) {
    problems.dte_min = sameDay
      ? "Use 0 (expiry day) to 45 days."
      : "Use 1 to 45 days.";
  }
  if (!inRange(entry.dte_max, 1, 45)) problems.dte_max = "Use 1 to 45 days.";
  if (!problems.dte_min && !problems.dte_max && entry.dte_min > entry.dte_max) {
    problems.dte_max = "Must not be below the minimum.";
  }
  if (entry.wait_move !== null && entry.wait_move !== undefined) {
    const [low, high] = LEG_UNIT_RANGES[entry.wait_unit ?? "pct"];
    if (!inRange(entry.wait_move, low, high))
      problems.wait_move = `Use ${upTo(low, high)}.`;
  }
  const trades = entry.max_trades_per_day;
  if (
    trades != null &&
    !(Number.isInteger(trades) && trades >= 1 && trades <= 50)
  ) {
    problems.max_trades_per_day = "Use 1 to 50, or leave empty.";
  }
  (entry.conditions ?? []).forEach((condition, index) => {
    if (condition.value === null || !Number.isFinite(condition.value))
      problems[`condition-${index}`] = "Enter a level.";
  });
}

function exitProblems(
  spec: LabSpec,
  problems: Problems,
  sameDay: boolean,
): void {
  const { entry, exits } = spec;
  const check = (
    key: string,
    value: number | null | undefined,
    unit: ExitUnit | undefined,
    optional = false,
  ) => {
    if (optional && (value === null || value === undefined)) return;
    const [low, high] = UNIT_RANGES[unit ?? "premium"];
    if (!inRange(value ?? null, low, high))
      problems[key] = `Use ${upTo(low, high)}.`;
  };
  check("target_frac", exits.target_frac, exits.target_unit);
  check("stop_frac", exits.stop_frac, exits.stop_unit);
  check("trail", exits.trail, exits.trail_unit, true);
  check("breakeven_at", exits.breakeven_at, exits.breakeven_unit, true);
  for (const key of ["daily_stop", "daily_target"] as const) {
    const value = exits[key];
    if (value !== null && value !== undefined && !(value > 0))
      problems[key] = "Use a positive amount, or leave empty.";
  }
  if (!inRange(exits.exit_dte, 0, 10)) problems.exit_dte = "Use 0 to 10 days.";
  const tooLate =
    entry.dte_min < exits.exit_dte ||
    (entry.dte_min === exits.exit_dte && !sameDay);
  if (!problems.dte_min && !problems.exit_dte && tooLate) {
    problems.exit_dte =
      "Must be below the minimum days to expiry, or no trade can be entered.";
  }
}

function budgetProblems(spec: LabSpec, problems: Problems): void {
  const budget = spec.budget ?? DEFAULT_BUDGET;
  if (!inRange(budget.capital, 10000, 100000000))
    problems.capital = "Use ₹10,000 or more.";
  if (!inRange(budget.risk_per_trade_pct, 0.1, 25))
    problems.risk_per_trade_pct = "Use 0.1% to 25%.";
  if (!inRange(budget.deploy_pct ?? 100, 1, 100))
    problems.deploy_pct = "Use 1% to 100%.";
  if (!inRange(budget.max_open, 1, 10) || !Number.isInteger(budget.max_open)) {
    problems.max_open = "Use a whole number from 1 to 10.";
  }
  if ((budget.vol_sizing ?? "off") === "atr") {
    if (!inRange(budget.vol_floor ?? 0.5, 0.1, 1))
      problems.vol_floor = "Use 0.1 to 1.";
    if (!inRange(budget.vol_cap ?? 1, 1, 2)) problems.vol_cap = "Use 1 to 2.";
  }
  const losses = budget.pause_after_losses ?? null;
  if (losses !== null && (!inRange(losses, 2, 20) || !Number.isInteger(losses)))
    problems.pause_after_losses =
      "Use a whole number from 2 to 20, or leave it blank.";
  const fall = budget.pause_drawdown_pct ?? null;
  if (fall !== null && !inRange(fall, 1, 100))
    problems.pause_drawdown_pct = "Use 1% to 100%, or leave it blank.";
  const pause = budget.pause_sessions ?? 5;
  if (!inRange(pause, 1, 60) || !Number.isInteger(pause))
    problems.pause_sessions = "Use a whole number from 1 to 60.";
}

/** The server checks these too. Here they point at the exact field while editing. */
export function findProblems(spec: LabSpec): Problems {
  const problems: Problems = {};
  if (!spec.name.trim()) problems.name = "Give the strategy a name.";
  legProblems(spec.legs, "leg", problems);
  (spec.cases ?? []).forEach((item, index) => {
    if (!item.name.trim()) problems[`case-${index}-name`] = "Name the case.";
    legProblems(item.legs, `case-${index}-leg`, problems);
  });
  const sameDay =
    (spec.decide_every ?? "day") !== "day" && !!spec.exits.square_off;
  entryProblems(spec, problems, sameDay);
  exitProblems(spec, problems, sameDay);
  budgetProblems(spec, problems);
  return problems;
}

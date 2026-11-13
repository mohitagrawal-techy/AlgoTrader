import "bootstrap/dist/css/bootstrap.min.css";
import { IndexChart } from "./IndexChart";
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  api,
  type ExitUnit,
  type LabCondition,
  type LabCase,
  type TechOperand,
  type LabLeg,
  type LabOverview,
  type LabSpec,
  type IndicatorPick,
  type LegUnit,
  type TechRule,
  type TechTimeframe,
} from "../api";
import { IndicatorForm } from "./IndicatorForm";
import { StrategyVisual } from "./StrategyVisual";
import {
  MAX_RULES,
  MINUTE_FRAMES,
  normalPick,
  operandPick,
  pickKey,
  pickText,
  PRICE_NAMES,
  ruleProblems,
  ruleText,
  TechRulesEditor,
} from "./TechRules";

const MAX_LEGS = 4;
const MAX_CONDITIONS = 6;
const MAX_RATIONALE = 600;
const TRENDS = ["up", "down", "range"] as const;
const BIASES = [
  { value: "bullish", label: "Bullish" },
  { value: "neutral", label: "Neutral" },
  { value: "bearish", label: "Bearish" },
] as const;
const DECISIONS = [
  { value: "day", label: "Once a day, at the close" },
  { value: "5m", label: "Every 5 minutes" },
  { value: "15m", label: "Every 15 minutes" },
  { value: "1h", label: "Every hour" },
] as const;

/** "09:30:00" from the server, "09:30" from an input: both shown as "09:30". */
function hhmm(value: string | null | undefined): string {
  return value ? value.slice(0, 5) : "";
}

const UNITS: { value: ExitUnit; label: string }[] = [
  { value: "premium", label: "× premium" },
  { value: "rupees", label: "₹ per lot" },
  { value: "points", label: "points" },
];
const UNIT_RANGES: Record<ExitUnit, [number, number]> = {
  premium: [0.05, 5],
  rupees: [1, 1_000_000],
  points: [0.05, 10_000],
};
// A sensible starting amount when the unit changes.
const UNIT_DEFAULTS: Record<ExitUnit, number> = { premium: 0.5, rupees: 2000, points: 20 };

function amountText(value: number | null | undefined, unit: ExitUnit | undefined): string {
  if (value === null || value === undefined) return "–";
  if (unit === "rupees") return `₹${value.toLocaleString("en-IN")}/lot`;
  if (unit === "points") return `${value} pts`;
  return `${value}× premium`;
}

/** An amount with its unit picker, as one input group. */
function AmountInput({
  id,
  value,
  unit,
  invalid,
  optional,
  onChange,
}: {
  id: string;
  value: number | null | undefined;
  unit: ExitUnit | undefined;
  invalid: boolean;
  optional?: boolean;
  onChange: (value: number | null, unit: ExitUnit) => void;
}) {
  const current = unit ?? "premium";
  return (
    <div className="input-group">
      <NumberInput
        id={id}
        value={value ?? null}
        step={current === "premium" ? 0.05 : current === "points" ? 1 : 100}
        placeholder={optional ? "Off" : undefined}
        invalid={invalid}
        onChange={(next) => onChange(next, current)}
      />
      <select
        className="form-select flex-grow-0 w-auto"
        aria-label="Unit"
        value={current}
        onChange={(event) => {
          const next = event.target.value as ExitUnit;
          onChange(value === null || value === undefined ? null : UNIT_DEFAULTS[next], next);
        }}
      >
        {UNITS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

const MAX_CASES = 3;
const SUMMARY_KEY = "algoverse.builder.summary";
const DEFAULT_BUDGET: NonNullable<LabSpec["budget"]> = {
  capital: 100000,
  risk_per_trade_pct: 5,
  max_open: 5,
  sizing: "budget",
  deploy_pct: 100,
};

const NEW_LEG: LabLeg = {
  action: "buy",
  option_type: "CE",
  ratio: 1,
  select: "delta",
  value: 0.5,
  expiry: 0,
};

export type EditorRoute = { mode: "new" } | { mode: "edit" | "copy"; id: string };

type Problems = Record<string, string>;

function blankSpec(): LabSpec {
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
  };
}

function inRange(value: number | null, low: number, high: number): boolean {
  return value !== null && Number.isFinite(value) && value >= low && value <= high;
}

function legProblems(legs: LabLeg[], prefix: string, problems: Problems): void {
  legs.forEach((leg, index) => {
    if (!inRange(leg.ratio, 1, 4) || !Number.isInteger(leg.ratio)) {
      problems[`${prefix}-${index}-ratio`] = "Lots: use a whole number from 1 to 4.";
    }
    if (leg.select === "delta" && !inRange(leg.value, 0.05, 0.95)) {
      problems[`${prefix}-${index}-value`] = "Strike: use a delta from 0.05 to 0.95.";
    }
    if (leg.select === "atm_offset" && (!inRange(leg.value, -20, 20) || !Number.isInteger(leg.value))) {
      problems[`${prefix}-${index}-value`] = "Strike: use a whole number of strikes from -20 to 20.";
    }
    if (leg.select === "premium" && !inRange(leg.value, 0.5, 5000)) {
      problems[`${prefix}-${index}-value`] = "Strike: use a premium of ₹0.5 to ₹5,000.";
    }
    for (const field of ["stop", "target"] as const) {
      const amount = leg[field];
      if (amount === null || amount === undefined) continue;
      const [low, high] = LEG_UNIT_RANGES[leg[`${field}_unit`] ?? "pct"];
      if (!inRange(amount, low, high)) {
        const name = field === "stop" ? "Stop loss" : "Take profit";
        problems[`${prefix}-${index}-${field}`] = `${name}: use ${low} to ${high.toLocaleString("en-IN")}, or leave empty.`;
      }
    }
  });
  const seen = new Set<string>();
  legs.forEach((leg, index) => {
    const key = [leg.option_type, leg.select, leg.value, leg.expiry].join("|");
    if (seen.has(key)) problems[`${prefix}-${index}-value`] = "Strike: another leg already uses it.";
    seen.add(key);
  });
}

/** The server checks these too. Here they point at the exact field while editing. */
function findProblems(spec: LabSpec): Problems {
  const problems: Problems = {};
  if (!spec.name.trim()) problems.name = "Give the strategy a name.";
  legProblems(spec.legs, "leg", problems);
  (spec.cases ?? []).forEach((item, index) => {
    if (!item.name.trim()) problems[`case-${index}-name`] = "Name the case.";
    legProblems(item.legs, `case-${index}-leg`, problems);
  });
  const { entry, exits } = spec;
  if (entry.iv_rank_min !== null && !inRange(entry.iv_rank_min, 0, 100)) {
    problems.iv_rank_min = "Use 0 to 100, or leave empty.";
  }
  if (entry.iv_rank_max !== null && !inRange(entry.iv_rank_max, 0, 100)) {
    problems.iv_rank_max = "Use 0 to 100, or leave empty.";
  }
  if (entry.iv_rank_min !== null && entry.iv_rank_max !== null && entry.iv_rank_min > entry.iv_rank_max) {
    problems.iv_rank_max = "Must not be below the minimum.";
  }
  // Expiry day itself (0) is allowed for intraday strategies that square off the same day.
  const sameDay = (spec.decide_every ?? "day") !== "day" && !!exits.square_off;
  if (!inRange(entry.dte_min, sameDay ? 0 : 1, 45)) {
    problems.dte_min = sameDay ? "Use 0 (expiry day) to 45 days." : "Use 1 to 45 days.";
  }
  if (!inRange(entry.dte_max, 1, 45)) problems.dte_max = "Use 1 to 45 days.";
  if (!problems.dte_min && !problems.dte_max && entry.dte_min > entry.dte_max) {
    problems.dte_max = "Must not be below the minimum.";
  }
  const check = (key: string, value: number | null | undefined, unit: ExitUnit | undefined, optional = false) => {
    if (optional && (value === null || value === undefined)) return;
    const [low, high] = UNIT_RANGES[unit ?? "premium"];
    if (!inRange(value ?? null, low, high)) problems[key] = `Use ${low} to ${high.toLocaleString("en-IN")}.`;
  };
  check("target_frac", exits.target_frac, exits.target_unit);
  check("stop_frac", exits.stop_frac, exits.stop_unit);
  check("trail", exits.trail, exits.trail_unit, true);
  check("breakeven_at", exits.breakeven_at, exits.breakeven_unit, true);
  if (entry.wait_move !== null && entry.wait_move !== undefined) {
    const [low, high] = LEG_UNIT_RANGES[entry.wait_unit ?? "pct"];
    if (!inRange(entry.wait_move, low, high)) problems.wait_move = `Use ${low} to ${high.toLocaleString("en-IN")}.`;
  }
  for (const key of ["daily_stop", "daily_target"] as const) {
    const value = exits[key];
    if (value !== null && value !== undefined && !(value > 0)) problems[key] = "Use a positive amount, or leave empty.";
  }
  const budget = spec.budget ?? DEFAULT_BUDGET;
  if (!inRange(budget.capital, 10000, 100000000)) problems.capital = "Use ₹10,000 or more.";
  if (!inRange(budget.risk_per_trade_pct, 0.1, 25)) problems.risk_per_trade_pct = "Use 0.1% to 25%.";
  if (!inRange(budget.deploy_pct ?? 100, 1, 100)) problems.deploy_pct = "Use 1% to 100%.";
  if (!inRange(budget.max_open, 1, 10) || !Number.isInteger(budget.max_open)) {
    problems.max_open = "Use a whole number from 1 to 10.";
  }
  const trades = entry.max_trades_per_day;
  if (trades !== null && trades !== undefined && !(Number.isInteger(trades) && trades >= 1 && trades <= 50)) {
    problems.max_trades_per_day = "Use 1 to 50, or leave empty.";
  }
  if (!inRange(exits.exit_dte, 0, 10)) problems.exit_dte = "Use 0 to 10 days.";
  if (
    !problems.dte_min &&
    !problems.exit_dte &&
    (entry.dte_min < exits.exit_dte || (entry.dte_min === exits.exit_dte && !sameDay))
  ) {
    problems.exit_dte = "Must be below the minimum days to expiry, or no trade can be entered.";
  }
  (entry.conditions ?? []).forEach((condition, index) => {
    if (!Number.isFinite(condition.value) || condition.value === null) {
      problems[`condition-${index}`] = "Enter a level.";
    }
  });
  return problems;
}

const LEG_UNIT_RANGES: Record<LegUnit, [number, number]> = { pct: [1, 500], points: [0.05, 10_000] };
const LEG_UNIT_DEFAULTS: Record<LegUnit, number> = { pct: 25, points: 20 };

/** A leg's own stop or target: an amount, empty for off, and % or points beside it. */
function LegLevel({
  id,
  label,
  value,
  unit,
  invalid,
  onChange,
}: {
  id: string;
  label: string;
  value: number | null | undefined;
  unit: LegUnit | undefined;
  invalid: boolean;
  onChange: (value: number | null, unit: LegUnit) => void;
}) {
  const current = unit ?? "pct";
  return (
    <div className="input-group input-group-sm leg-level">
      <NumberInput
        id={id}
        value={value ?? null}
        step={current === "pct" ? 5 : 1}
        placeholder="Off"
        invalid={invalid}
        onChange={(next) => onChange(next, current)}
      />
      <select
        className="form-select flex-grow-0"
        aria-label={`${label} unit`}
        value={current}
        onChange={(event) => {
          const next = event.target.value as LegUnit;
          onChange(value === null || value === undefined ? null : LEG_UNIT_DEFAULTS[next], next);
        }}
      >
        <option value="pct">%</option>
        <option value="points">pts</option>
      </select>
    </div>
  );
}

/** Legs as a table: one row per option, with its own stop loss and take profit. */
function LegsEditor({
  legs,
  problems,
  onChange,
  prefix = "leg",
}: {
  legs: LabLeg[];
  problems: Problems;
  onChange: (legs: LabLeg[]) => void;
  prefix?: string;
}) {
  const setLeg = (index: number, change: Partial<LabLeg>) =>
    onChange(legs.map((leg, i) => (i === index ? { ...leg, ...change } : leg)));
  const legProblem = (index: number) =>
    ["ratio", "value", "stop", "target"]
      .map((field) => problems[`${prefix}-${index}-${field}`])
      .filter(Boolean);
  return (
    <>
      <div className="leg-rows">
        {legs.map((leg, index) => {
          const id = `${prefix}-${index}`;
          const found = legProblem(index);
          return (
            <div key={index} className={`leg-row${found.length ? " has-problem" : ""}`} role="group" aria-label={`Leg ${index + 1}`}>
              <span className="leg-badge badge text-bg-primary">Leg {index + 1}</span>
              <div className="leg-cell leg-cell-action">
                <label className="leg-label" htmlFor={`${id}-action`}>Action</label>
                <select
                  id={`${id}-action`}
                  className={`form-select form-select-sm leg-action is-${leg.action}`}
                  value={leg.action}
                  onChange={(event) => setLeg(index, { action: event.target.value as LabLeg["action"] })}
                >
                  <option value="buy">Buy</option>
                  <option value="sell">Sell</option>
                </select>
              </div>
              <div className="leg-cell leg-cell-type">
                <label className="leg-label" htmlFor={`${id}-type`}>Type</label>
                <select
                  id={`${id}-type`}
                  className="form-select form-select-sm"
                  value={leg.option_type}
                  onChange={(event) => setLeg(index, { option_type: event.target.value as LabLeg["option_type"] })}
                >
                  <option value="CE">CE</option>
                  <option value="PE">PE</option>
                </select>
              </div>
              <div className="leg-cell leg-cell-expiry">
                <label className="leg-label" htmlFor={`${id}-expiry`}>Expiry</label>
                <select
                  id={`${id}-expiry`}
                  className="form-select form-select-sm"
                  value={leg.expiry}
                  onChange={(event) => setLeg(index, { expiry: Number(event.target.value) })}
                >
                  <option value={0}>Entry expiry</option>
                  <option value={1}>Next expiry</option>
                  <option value={2}>Expiry after next</option>
                </select>
              </div>
              <div className="leg-cell leg-cell-strike">
                <label className="leg-label" htmlFor={`${id}-select`}>Strike by</label>
                <div className="input-group input-group-sm">
                  <select
                    id={`${id}-select`}
                    className="form-select"
                    value={leg.select}
                    onChange={(event) => {
                      const select = event.target.value as LabLeg["select"];
                      setLeg(index, { select, value: select === "delta" ? 0.3 : select === "premium" ? 100 : 0 });
                    }}
                  >
                    <option value="atm_offset">ATM ± strikes</option>
                    <option value="delta">Delta</option>
                    <option value="premium">Premium ₹</option>
                  </select>
                  <label className="visually-hidden" htmlFor={`${id}-value`}>
                    {leg.select === "delta" ? "Delta" : leg.select === "premium" ? "Premium in rupees" : "Strikes from ATM"}
                  </label>
                  <NumberInput
                    id={`${id}-value`}
                    value={leg.value}
                    step={leg.select === "delta" ? 0.05 : leg.select === "premium" ? 5 : 1}
                    invalid={!!problems[`${id}-value`]}
                    onChange={(value) => setLeg(index, { value: value as number })}
                  />
                </div>
              </div>
              <div className="leg-cell leg-cell-lots">
                <label className="leg-label" htmlFor={`${id}-ratio`}>Lots</label>
                <NumberInput
                  id={`${id}-ratio`}
                  value={leg.ratio}
                  invalid={!!problems[`${id}-ratio`]}
                  onChange={(value) => setLeg(index, { ratio: value as number })}
                />
              </div>
              <div className="leg-cell leg-cell-level">
                <label className="leg-label" htmlFor={`${id}-stop`}>Stop loss</label>
                <LegLevel
                  id={`${id}-stop`}
                  label={`Leg ${index + 1} stop loss`}
                  value={leg.stop}
                  unit={leg.stop_unit}
                  invalid={!!problems[`${id}-stop`]}
                  onChange={(stop, stop_unit) => setLeg(index, { stop, stop_unit })}
                />
              </div>
              <div className="leg-cell leg-cell-level">
                <label className="leg-label" htmlFor={`${id}-target`}>Take profit</label>
                <LegLevel
                  id={`${id}-target`}
                  label={`Leg ${index + 1} take profit`}
                  value={leg.target}
                  unit={leg.target_unit}
                  invalid={!!problems[`${id}-target`]}
                  onChange={(target, target_unit) => setLeg(index, { target, target_unit })}
                />
              </div>
              {legs.length > 1 && (
                <button
                  type="button"
                  className="btn btn-sm btn-link text-danger leg-remove"
                  aria-label={`Remove leg ${index + 1}`}
                  onClick={() => onChange(legs.filter((_, i) => i !== index))}
                >
                  Remove
                </button>
              )}
            </div>
          );
        })}
      </div>
      {legs.map((_, index) =>
        legProblem(index).map((text) => (
          <div className="invalid-feedback d-block" key={`${index}-${text}`}>
            Leg {index + 1}: {text}
          </div>
        )),
      )}
      <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mt-2">
        <small className="text-body-secondary">
          Lots set the ratio between legs; the budget decides how many sets to trade. A leg's own stop loss or take profit closes only that leg.
        </small>
        {legs.length < MAX_LEGS && (
          <button
            type="button"
            className="btn btn-outline-primary btn-sm"
            onClick={() => onChange([...legs, { ...NEW_LEG, value: 0.3 }])}
          >
            Add leg
          </button>
        )}
      </div>
    </>
  );
}

/** The other side of a case: calls become puts, "above" becomes "below", highs become lows.
 * For a breakout that buys calls above a range, this is the put bought below it. */
function mirrorRule(rule: TechRule): TechRule {
  if (rule.type !== "compare") return rule;
  const flip: Record<string, string> = {
    above: "below",
    below: "above",
    crosses_above: "crosses_below",
    crosses_below: "crosses_above",
  };
  const fields: Record<string, string> = { high: "low", low: "high", upper: "lower", lower: "upper" };
  const side = (operand: TechOperand): TechOperand =>
    operand.field && fields[operand.field] ? { ...operand, field: fields[operand.field] } : operand;
  return { ...rule, op: flip[rule.op] as typeof rule.op, left: side(rule.left), right: side(rule.right) };
}

function oppositeCase(spec: LabSpec): LabCase {
  const bias = spec.bias === "bullish" ? "bearish" : spec.bias === "bearish" ? "bullish" : "neutral";
  return {
    name: spec.legs.every((leg) => leg.option_type === "CE") ? "Put side" : "Call side",
    bias,
    legs: spec.legs.map((leg) => ({
      ...leg,
      option_type: leg.option_type === "CE" ? "PE" : "CE",
      value: leg.select === "atm_offset" ? -leg.value : leg.value,
    })),
    rules: (spec.entry.rules ?? []).map(mirrorRule),
    any_rules: (spec.entry.any_rules ?? []).map(mirrorRule),
  };
}

function legText(leg: LabLeg): string {
  const strike =
    leg.select === "delta"
      ? `delta ${leg.value}`
      : leg.select === "premium"
        ? `premium near ₹${leg.value}`
        : leg.value
          ? `ATM${leg.value > 0 ? "+" : ""}${leg.value}`
          : "ATM";
  const later = leg.expiry ? `, expiry +${leg.expiry}` : "";
  const level = (amount: number | null | undefined, unit: LegUnit | undefined) =>
    unit === "points" ? `${amount} pts` : `${amount}%`;
  const own = [
    leg.stop != null ? `SL ${level(leg.stop, leg.stop_unit)}` : "",
    leg.target != null ? `TP ${level(leg.target, leg.target_unit)}` : "",
  ].filter(Boolean);
  const levels = own.length ? ` (${own.join(", ")})` : "";
  return `${leg.ratio} ${leg.option_type === "CE" ? "Call" : "Put"} ${strike}${later}${levels}`;
}

function complexity(spec: LabSpec): { label: string; tone: string } {
  const { entry } = spec;
  const hasIv = entry.iv_rank_min !== null || entry.iv_rank_max !== null;
  const filters =
    Number(entry.trend.length > 0) + Number(hasIv) + (entry.conditions ?? []).length +
    (entry.rules ?? []).length +
    Number((entry.any_rules ?? []).length > 0);
  if (spec.legs.length >= 3 || filters > 2) return { label: "Complex", tone: "text-bg-primary" };
  if (spec.legs.length === 2 || filters > 1) return { label: "Moderate", tone: "text-bg-info" };
  return { label: "Simple", tone: "text-bg-secondary" };
}

function sellsMoreThanBuys(spec: LabSpec): boolean {
  const net = { CE: 0, PE: 0 };
  for (const leg of spec.legs) net[leg.option_type] += (leg.action === "buy" ? 1 : -1) * leg.ratio;
  return net.CE < 0 || net.PE < 0;
}

function useBootstrapTheme(): "light" | "dark" {
  const read = () => {
    const chosen = document.documentElement.dataset.theme;
    if (chosen === "light" || chosen === "dark") return chosen;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  };
  const [theme, setTheme] = useState<"light" | "dark">(read);
  useEffect(() => {
    const update = () => setTheme(read());
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", update);
    return () => {
      observer.disconnect();
      media.removeEventListener("change", update);
    };
  }, []);
  return theme;
}

function NumberInput({
  id,
  value,
  onChange,
  invalid,
  step = 1,
  placeholder,
}: {
  id: string;
  value: number | null;
  onChange: (value: number | null) => void;
  invalid?: boolean;
  step?: number;
  placeholder?: string;
}) {
  const [text, setText] = useState(value === null ? "" : String(value));
  // Keeps half-typed text such as "0." unless the value was changed from outside.
  useEffect(() => {
    const typed = text === "" ? null : Number(text);
    if (typed !== value) setText(value === null ? "" : String(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <input
      id={id}
      type="number"
      className={`form-control${invalid ? " is-invalid" : ""}`}
      value={text}
      step={step}
      placeholder={placeholder}
      onChange={(event) => {
        setText(event.target.value);
        onChange(event.target.value === "" ? null : Number(event.target.value));
      }}
    />
  );
}

function Field({
  id,
  label,
  hint,
  problem,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  problem?: string;
  children: ReactNode;
}) {
  return (
    <>
      <label className="form-label" htmlFor={id}>
        {label}
      </label>
      {children}
      {problem && <div className="invalid-feedback d-block">{problem}</div>}
      {hint && !problem && <div className="form-text">{hint}</div>}
    </>
  );
}

function Toggle<T extends string>({
  name,
  label,
  value,
  options,
  onChange,
}: {
  name: string;
  label: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="btn-group w-100" role="group" aria-label={label}>
      {options.map((option) => (
        <span key={option.value} className="d-contents">
          <input
            className="btn-check"
            type="radio"
            name={name}
            id={`${name}-${option.value}`}
            checked={value === option.value}
            onChange={() => onChange(option.value)}
          />
          <label className="btn btn-outline-secondary" htmlFor={`${name}-${option.value}`}>
            {option.label}
          </label>
        </span>
      ))}
    </div>
  );
}

/** One optional feature: a switch, its explanation, and its settings while it is on. */
function FeatureSwitch({
  id,
  label,
  hint,
  checked,
  disabled,
  onChange,
  children,
}: {
  id: string;
  label: string;
  hint: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (on: boolean) => void;
  children?: ReactNode;
}) {
  return (
    <div className={`feature${checked && !disabled ? " is-on" : ""}${disabled ? " is-disabled" : ""}`}>
      <div className="form-check form-switch mb-0">
        <input
          id={id}
          className="form-check-input"
          type="checkbox"
          role="switch"
          checked={checked && !disabled}
          disabled={disabled}
          aria-describedby={`${id}-hint`}
          onChange={(event) => onChange(event.target.checked)}
        />
        <label className="form-check-label fw-semibold" htmlFor={id}>
          {label}
        </label>
      </div>
      <small id={`${id}-hint`} className="text-body-secondary d-block">
        {hint}
      </small>
      {checked && !disabled && children && <div className="mt-2">{children}</div>}
    </div>
  );
}

const SECTIONS_KEY = "algoverse.builder.collapsed";
const SECTION_TITLES = [
  "Start from",
  "Basics",
  "Budget",
  "Indicators",
  "Features",
  "Cases",
  "Enter when",
  "Exit when",
];

/** Which builder sections are folded, by title, shared so one control can fold them all. */
const FoldContext = createContext<{ folded: Set<string>; toggle: (title: string) => void }>({
  folded: new Set(),
  toggle: () => undefined,
});

function readFolded(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(SECTIONS_KEY) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

function Section({ title, hint, aside, children }: { title: string; hint?: string; aside?: ReactNode; children: ReactNode }) {
  const { folded, toggle } = useContext(FoldContext);
  const open = !folded.has(title);
  const bodyId = `section-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <section className={`card mb-3 builder-section${open ? "" : " is-folded"}`}>
      <div className="card-header d-flex justify-content-between align-items-center gap-2">
        <button
          type="button"
          className="builder-section-toggle"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => toggle(title)}
        >
          <span className="builder-chevron" aria-hidden>
            ▸
          </span>
          <span>
            <span className="h6 mb-0 d-block">{title}</span>
            {hint && open && <small className="text-body-secondary d-block">{hint}</small>}
          </span>
        </button>
        {aside}
      </div>
      {/* Hidden, not removed, so the chart keeps its data and a replay its place. */}
      <div className="card-body" id={bodyId} hidden={!open}>
        {children}
      </div>
    </section>
  );
}

/** Every rule list in a strategy, the strategy's own first, then each case's. */
function allRuleLists(spec: LabSpec): TechRule[][] {
  return [
    spec.entry.rules ?? [],
    spec.entry.any_rules ?? [],
    spec.exits.rules ?? [],
    ...(spec.cases ?? []).flatMap((item) => [item.rules ?? [], item.any_rules ?? [], item.exit_rules ?? []]),
  ];
}

/** The same strategy with every rule list passed through `change`. */
function mapRuleLists(spec: LabSpec, change: (rules: TechRule[]) => TechRule[]): LabSpec {
  return {
    ...spec,
    entry: { ...spec.entry, rules: change(spec.entry.rules ?? []), any_rules: change(spec.entry.any_rules ?? []) },
    exits: { ...spec.exits, rules: change(spec.exits.rules ?? []) },
    cases: (spec.cases ?? []).map((item) => ({
      ...item,
      rules: change(item.rules ?? []),
      any_rules: change(item.any_rules ?? []),
      exit_rules: item.exit_rules == null ? item.exit_rules : change(item.exit_rules),
    })),
  };
}

function operandsOf(rule: TechRule): TechOperand[] {
  return rule.type === "compare" ? [rule.left, rule.right] : [];
}

export function StrategyEditor({ route }: { route: EditorRoute }) {
  const theme = useBootstrapTheme();
  const [lab, setLab] = useState<LabOverview | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [spec, setSpec] = useState<LabSpec>(blankSpec);
  const [missing, setMissing] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mapOpen, setMapOpen] = useState(false);
  const [chartOpen, setChartOpen] = useState(false);
  const [activeCase, setActiveCase] = useState(0);
  // The indicator form: closed, adding (null) or changing one indicator.
  const [indicatorForm, setIndicatorForm] = useState<{ editing: IndicatorPick | null } | null>(null);
  const [folded, setFolded] = useState<Set<string>>(readFolded);
  const saveFolded = (next: Set<string>) => {
    setFolded(next);
    try {
      localStorage.setItem(SECTIONS_KEY, JSON.stringify([...next]));
    } catch {
      // Remembered for this visit only when storage is blocked.
    }
  };
  const fold = useMemo(
    () => ({
      folded,
      toggle: (title: string) => {
        const next = new Set(folded);
        if (next.has(title)) next.delete(title);
        else next.add(title);
        saveFolded(next);
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [folded],
  );
  // The summary is a sidebar that folds away, remembered in this browser.
  const [summaryOpen, setSummaryOpen] = useState(() => {
    try {
      return localStorage.getItem(SUMMARY_KEY) !== "closed";
    } catch {
      return true;
    }
  });
  const toggleSummary = (open: boolean) => {
    setSummaryOpen(open);
    try {
      localStorage.setItem(SUMMARY_KEY, open ? "open" : "closed");
    } catch {
      // Remembered for this visit only when storage is blocked.
    }
  };
  const mapDialog = useRef<HTMLDialogElement>(null);
  const chartDialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = mapDialog.current;
    if (!dialog) return;
    if (mapOpen && !dialog.open) dialog.showModal();
    if (!mapOpen && dialog.open) dialog.close();
  }, [mapOpen]);

  useEffect(() => {
    const dialog = chartDialog.current;
    if (!dialog) return;
    if (chartOpen && !dialog.open) dialog.showModal();
    if (!chartOpen && dialog.open) dialog.close();
  }, [chartOpen]);

  useEffect(() => {
    let active = true;
    api
      .lab()
      .then((body) => {
        if (!active) return;
        setLab(body);
        if (route.mode === "new") return;
        const row = body.strategies.find((item) => item.spec.id === route.id);
        if (!row) {
          setMissing(true);
        } else if (route.mode === "copy") {
          setSpec({ ...row.spec, id: undefined, source: undefined, name: `${row.spec.name} copy`.slice(0, 60) });
        } else {
          setSpec(row.spec);
        }
      })
      .catch(() => active && setLoadFailed(true));
    return () => {
      active = false;
    };
  }, [route]);

  const rules: TechRule[] = spec.entry.rules ?? [];
  const exitRules: TechRule[] = spec.exits.rules ?? [];
  const anyRules: TechRule[] = spec.entry.any_rules ?? [];
  // The strategy's indicators: those picked, and any its rules already use (older strategies
  // were saved without a list), each once.
  const { picks, useCount } = useMemo(() => {
    const found: IndicatorPick[] = [];
    const counts: Record<string, number> = {};
    if (!lab) return { picks: found, useCount: counts };
    const seen = new Set<string>();
    const add = (pick: IndicatorPick) => {
      const full = normalPick(lab.technical, pick.name, pick.params, pick);
      const key = pickKey(lab.technical, full);
      if (!seen.has(key)) {
        seen.add(key);
        found.push(full);
      }
      return key;
    };
    for (const pick of spec.indicators ?? []) add(pick);
    for (const list of allRuleLists(spec)) {
      for (const rule of list) {
        for (const operand of operandsOf(rule)) {
          if (operand.kind !== "indicator" || !operand.name || PRICE_NAMES.includes(operand.name)) continue;
          // An indicator a rule reads is on that rule's candles.
          const key = add(operandPick(operand, rule));
          counts[key] = (counts[key] ?? 0) + 1;
        }
      }
    }
    return { picks: found, useCount: counts };
  }, [spec, lab]);
  const intraday = (spec.decide_every ?? "day") !== "day";
  const problems = useMemo(() => {
    const found: Record<string, string> = {
      ...findProblems(spec),
      ...(lab ? ruleProblems(lab.technical, spec.entry.rules ?? []) : {}),
      ...(lab ? ruleProblems(lab.technical, spec.exits.rules ?? [], "exit-rule") : {}),
      ...(lab ? ruleProblems(lab.technical, spec.entry.any_rules ?? [], "any-rule") : {}),
    };
    const daily = (spec.decide_every ?? "day") === "day";
    const lists: [string, TechRule[]][] = [
      ["rule", spec.entry.rules ?? []],
      ["any-rule", spec.entry.any_rules ?? []],
      ["exit-rule", spec.exits.rules ?? []],
    ];
    (spec.cases ?? []).forEach((item, index) => {
      lists.push([`case-${index}-rule`, item.rules ?? []]);
      lists.push([`case-${index}-any-rule`, item.any_rules ?? []]);
      lists.push([`case-${index}-exit-rule`, item.exit_rules ?? []]);
    });
    if (lab) {
      for (const [prefix, list] of lists.slice(3)) Object.assign(found, ruleProblems(lab.technical, list, prefix));
    }
    for (const [prefix, list] of lists) {
      list.forEach((rule, index) => {
        if (daily && MINUTE_FRAMES.includes(rule.timeframe)) {
          found[`${prefix}-${index}-left`] = "Minute candles need a decision every 5, 15 or 60 minutes.";
        }
      });
    }
    const from = hhmm(spec.entry.time_from);
    const to = hhmm(spec.entry.time_to);
    const off = hhmm(spec.exits.square_off);
    const inSession = (t: string) => !t || (t >= "09:15" && t <= "15:25");
    if (!inSession(from)) found.time_from = "Use 09:15 to 15:25.";
    if (!inSession(to)) found.time_to = "Use 09:15 to 15:25.";
    if (from && to && from >= to) found.time_to = "Must be after the start.";
    if (off && !(off > "09:15" && off <= "15:25")) found.square_off = "Use 09:20 to 15:25.";
    return found;
  }, [spec, lab]);
  const setDecision = (value: LabSpec["decide_every"]) =>
    setSpec((current) =>
      value === "day"
        ? {
            ...current,
            decide_every: value,
            entry: { ...current.entry, time_from: null, time_to: null, wait_move: null },
            exits: { ...current.exits, square_off: null },
          }
        : { ...current, decide_every: value },
    );
  const visible: Problems = attempted ? problems : { ...problems, name: "" };
  const conditions = spec.entry.conditions ?? [];
  const level = complexity(spec);
  const backHref = route.mode === "edit" ? `#lab/view/${route.id}` : "#lab";

  const setEntry = (change: Partial<LabSpec["entry"]>) =>
    setSpec((current) => ({ ...current, entry: { ...current.entry, ...change } }));
  const setExits = (change: Partial<LabSpec["exits"]>) =>
    setSpec((current) => ({ ...current, exits: { ...current.exits, ...change } }));
  const cases: LabCase[] = spec.cases ?? [];
  const budget = spec.budget ?? DEFAULT_BUDGET;
  const setBudget = (change: Partial<NonNullable<LabSpec["budget"]>>) =>
    setSpec((current) => ({ ...current, budget: { ...(current.budget ?? DEFAULT_BUDGET), ...change } }));
  const setCase = (index: number, change: Partial<LabCase>) =>
    setSpec((current) => ({
      ...current,
      cases: (current.cases ?? []).map((item, i) => (i === index ? { ...item, ...change } : item)),
    }));
  const setCondition = (index: number, change: Partial<LabCondition>) =>
    setEntry({ conditions: conditions.map((item, i) => (i === index ? { ...item, ...change } : item)) });

  const addPick = (pick: IndicatorPick) =>
    setSpec((current) => ({ ...current, indicators: [...picks, pick] }));
  /** New settings, candles or name for a picked indicator, in the list and in every rule
   * that reads it: those rules move to its new candles. */
  const replacePick = (old: IndicatorPick, next: IndicatorPick) => {
    if (!lab) return;
    const oldKey = pickKey(lab.technical, old);
    const reads = (operand: TechOperand, rule: TechRule) =>
      operand.kind === "indicator" &&
      operand.name === old.name &&
      pickKey(lab.technical, operandPick(operand, rule)) === oldKey;
    const swap = (operand: TechOperand, rule: TechRule): TechOperand =>
      reads(operand, rule) ? { ...operand, params: { ...next.params } } : operand;
    const move = (rule: TechRule): TechRule =>
      rule.type === "compare" && (reads(rule.left, rule) || reads(rule.right, rule))
        ? {
            ...rule,
            left: swap(rule.left, rule),
            right: swap(rule.right, rule),
            timeframe: next.timeframe ?? "1d",
            candles: next.candles ?? "normal",
          }
        : rule;
    setSpec((current) => ({
      ...mapRuleLists(current, (rules) => rules.map(move)),
      indicators: picks.map((pick) => (pickKey(lab.technical, pick) === oldKey ? next : pick)),
    }));
  };

  // Case tabs: 0 is the strategy's own legs and rules, then each further case.
  const caseCount = 1 + cases.length;
  const active = Math.min(activeCase, cases.length);
  const activeRules = active === 0 ? rules : (cases[active - 1].rules ?? []);
  const activeAnyRules = active === 0 ? anyRules : (cases[active - 1].any_rules ?? []);
  const activeExitRules = active === 0 ? exitRules : (cases[active - 1].exit_rules ?? []);
  const activeLegs = active === 0 ? spec.legs : cases[active - 1].legs;
  const addCase = (item: LabCase) => {
    setSpec((current) => ({ ...current, cases: [...(current.cases ?? []), item] }));
    setActiveCase(caseCount);
  };
  const duplicateCase = () => {
    const source: LabCase =
      active === 0
        ? { name: `Case ${caseCount + 1}`, bias: spec.bias, legs: spec.legs, rules, any_rules: anyRules, exit_rules: null }
        : { ...cases[active - 1], name: `${cases[active - 1].name} copy`.slice(0, 40) };
    addCase({ ...source, legs: source.legs.map((leg) => ({ ...leg })) });
  };
  const removeCase = (index: number) => {
    setSpec((current) => ({ ...current, cases: (current.cases ?? []).filter((_, i) => i !== index) }));
    setActiveCase(index);
  };
  // The tab a problem's field sits on: "case-N-..." on case N+1, the strategy's own on Case 1.
  const tabOf = (key: string): number | null => {
    const match = /^case-(\d+)-/.exec(key);
    if (match) return Number(match[1]) + 1;
    return /^(leg|rule|any-rule|exit-rule)-/.test(key) ? 0 : null;
  };
  const casesWithProblems = new Set(
    Object.entries(visible)
      .filter(([, text]) => text)
      .map(([key]) => tabOf(key))
      .filter((tab): tab is number => tab !== null),
  );
  const featuresOn = [
    spec.exits.trail != null,
    spec.exits.breakeven_at != null,
    spec.entry.reenter_after_stop ?? true,
    spec.entry.reenter_after_target ?? true,
    intraday && spec.entry.wait_move != null,
  ].filter(Boolean).length;

  const applyStart = (id: string) => {
    if (!id) {
      setSpec(blankSpec());
      return;
    }
    const row = lab?.strategies.find((item) => item.spec.id === id);
    if (row) {
      setSpec({ ...row.spec, id: undefined, source: undefined, name: `${row.spec.name} copy`.slice(0, 60) });
    }
  };

  const save = async (thenTest: boolean) => {
    setAttempted(true);
    if (Object.keys(problems).length) {
      // Open every section, so no marked field hides inside a folded one, and show the
      // first case tab with a problem.
      saveFolded(new Set());
      const tab = Object.keys(problems).map(tabOf).find((found) => found !== null);
      if (tab !== undefined && tab !== null) setActiveCase(tab);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const saved = await api.labSave({ ...spec, name: spec.name.trim(), indicators: picks });
      if (thenTest) await api.labBacktest([saved.id as string]);
      window.location.hash = `lab/view/${saved.id}`;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save.");
      setBusy(false);
    }
  };

  if (loadFailed || missing) {
    return (
      <div className="bs-scope strategy-page" data-bs-theme={theme}>
        <div className="alert alert-danger">
          {missing ? "That strategy no longer exists." : "Could not load the strategy lab."}{" "}
          <a href="#lab" className="alert-link">
            Back to the lab
          </a>
        </div>
      </div>
    );
  }
  if (!lab) {
    return (
      <div className="bs-scope strategy-page" data-bs-theme={theme}>
        <div className="d-flex align-items-center gap-2 p-3">
          <div className="spinner-border spinner-border-sm" role="status" aria-hidden />
          Loading…
        </div>
      </div>
    );
  }

  const title = route.mode === "edit" ? "Edit strategy" : "New strategy";
  const templates = lab.strategies.filter((row) => row.spec.source === "template");
  return (
    <FoldContext.Provider value={fold}>
    <div className="bs-scope strategy-page" data-bs-theme={theme}>
      <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-2">
        <nav aria-label="breadcrumb">
          <ol className="breadcrumb mb-0">
            <li className="breadcrumb-item">
              <a href="#lab">Strategy lab</a>
            </li>
            <li className="breadcrumb-item active" aria-current="page">
              {title}
            </li>
          </ol>
        </nav>
        <div className="d-flex gap-2">
          <div className="btn-group btn-group-sm" role="group" aria-label="Sections">
            <button
              type="button"
              className="btn btn-outline-secondary"
              onClick={() => saveFolded(new Set(SECTION_TITLES))}
            >
              Collapse all
            </button>
            <button type="button" className="btn btn-outline-secondary" onClick={() => saveFolded(new Set())}>
              Expand all
            </button>
          </div>
          <button
            type="button"
            className="btn btn-outline-secondary btn-sm"
            aria-expanded={summaryOpen}
            aria-controls="strategy-summary"
            onClick={() => toggleSummary(!summaryOpen)}
          >
            {summaryOpen ? "Hide summary" : "Show summary"}
          </button>
          <button type="button" className="btn btn-primary btn-sm" onClick={() => setChartOpen(true)}>
            Preview chart
          </button>
          <button type="button" className="btn btn-outline-primary btn-sm" onClick={() => setMapOpen(true)}>
            Preview strategy map
          </button>
        </div>
      </div>

      <dialog
        ref={mapDialog}
        className="map-preview"
        aria-labelledby="map-preview-title"
        onClose={() => setMapOpen(false)}
        onClick={(event) => {
          // A click on the backdrop, outside the dialog's box, closes it.
          if (event.target === event.currentTarget) setMapOpen(false);
        }}
      >
        <div className="map-preview-box">
          <div className="map-preview-head">
            <div>
              <h2 id="map-preview-title" className="h5 mb-0">
                {spec.name.trim() || "Strategy"} map
              </h2>
              <small className="text-body-secondary">Updates as you edit. Esc closes it.</small>
            </div>
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => setMapOpen(false)}>
              Close
            </button>
          </div>
          <div className="map-preview-body">
            {mapOpen && <StrategyVisual spec={spec} catalog={lab?.technical ?? null} signals={lab?.signals} />}
          </div>
        </div>
      </dialog>

      <dialog
        ref={chartDialog}
        className="map-preview chart-preview"
        aria-labelledby="chart-preview-title"
        onClose={() => setChartOpen(false)}
        onClick={(event) => {
          if (event.target === event.currentTarget) setChartOpen(false);
        }}
      >
        <div className="map-preview-box">
          <div className="map-preview-head">
            <div>
              <h2 id="chart-preview-title" className="h5 mb-0">
                {spec.name.trim() || "Strategy"} on the chart
              </h2>
              <small className="text-body-secondary">
                {picks.length
                  ? `Its ${picks.length} indicator${picks.length === 1 ? "" : "s"} and levels are drawn, and arrows mark candles where each case's entry rules held. Esc closes it.`
                  : "Pick indicators under Indicators and they are drawn here. Arrows mark candles where the entry rules held. Esc closes it."}
              </small>
            </div>
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => setChartOpen(false)}>
              Close
            </button>
          </div>
          <div className="map-preview-body">
            {/* Mounted only while open, so it opens at full width with the draft as it is now. */}
            {chartOpen && (
              <IndexChart
                strategyPreview
                catalog={lab.technical}
                symbols={lab.chart_symbols ?? lab.symbols}
                tradable={lab.symbols}
                draft={{ ...spec, name: spec.name.trim() || "Draft", indicators: picks }}
                rules={rules}
                anyRules={anyRules}
                onAddRule={
                  activeRules.length < MAX_RULES
                    ? (rule) =>
                        active === 0
                          ? setEntry({ rules: [...rules, rule] })
                          : setCase(active - 1, { rules: [...activeRules, rule] })
                    : undefined
                }
              />
            )}
          </div>
        </div>
      </dialog>



      {!summaryOpen && (
        <button
          type="button"
          className="summary-rail"
          aria-expanded={false}
          aria-controls="strategy-summary"
          onClick={() => toggleSummary(true)}
          title="Show the summary"
        >
          <span aria-hidden>‹</span>
          <span className="summary-rail-label">Summary</span>
        </button>
      )}

      <div className="row g-3">
        <div className={summaryOpen ? "col-12 col-xl-8" : "col-12 summary-folded"}>
          {route.mode === "new" && (
            <Section title="Start from" hint="Begin blank, or copy the rules of a standard strategy.">
              <label className="visually-hidden" htmlFor="start-from">
                Start from
              </label>
              <select id="start-from" className="form-select" defaultValue="" onChange={(event) => applyStart(event.target.value)}>
                <option value="">Blank strategy</option>
                {templates.map((row) => (
                  <option key={row.spec.id} value={row.spec.id}>
                    {row.spec.name} ({row.legs_text})
                  </option>
                ))}
              </select>
            </Section>
          )}

          <Section title="Basics">
            <div className="row g-3">
              <div className="col-12 col-md-7">
                <Field id="name" label="Name" problem={visible.name}>
                  <input
                    id="name"
                    className={`form-control${visible.name ? " is-invalid" : ""}`}
                    maxLength={60}
                    value={spec.name}
                    placeholder="For example: Bull call spread in uptrends"
                    onChange={(event) => setSpec({ ...spec, name: event.target.value })}
                  />
                </Field>
              </div>
              <div className="col-12 col-md-5">
                <span className="form-label d-block">Market view</span>
                <Toggle name="bias" label="Market view" value={spec.bias} options={BIASES} onChange={(bias) => setSpec({ ...spec, bias })} />
              </div>
              <div className="col-12">
                <span className="form-label d-block">Trade on</span>
                {lab.symbols.map((symbol) => (
                  <div className="form-check form-check-inline" key={symbol}>
                    <input
                      id={`symbol-${symbol}`}
                      className="form-check-input"
                      type="checkbox"
                      checked={spec.symbols.includes(symbol)}
                      onChange={(event) =>
                        setSpec({
                          ...spec,
                          symbols: event.target.checked
                            ? [...spec.symbols, symbol]
                            : spec.symbols.filter((item) => item !== symbol),
                        })
                      }
                    />
                    <label className="form-check-label" htmlFor={`symbol-${symbol}`}>
                      {symbol}
                    </label>
                  </div>
                ))}
                <div className="form-text">None ticked means every index.</div>
              </div>
              <div className="col-12 col-md-7">
                <Field
                  id="decide"
                  label="Decide when to trade"
                  hint={
                    intraday
                      ? "Checked through the session; a trade enters at the next check. Uses minute data, NIFTY only for now."
                      : "Rules are checked at the close and a trade enters at the next open."
                  }
                >
                  <select
                    id="decide"
                    className="form-select"
                    value={spec.decide_every ?? "day"}
                    onChange={(event) => setDecision(event.target.value as LabSpec["decide_every"])}
                  >
                    {DECISIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              <div className="col-12">
                <Field id="rationale" label="Why should this work?" hint={`${spec.rationale.length} of ${MAX_RATIONALE} characters. Optional.`}>
                  <textarea
                    id="rationale"
                    className="form-control"
                    rows={2}
                    maxLength={MAX_RATIONALE}
                    value={spec.rationale}
                    onChange={(event) => setSpec({ ...spec, rationale: event.target.value })}
                  />
                </Field>
              </div>
            </div>
          </Section>

          <Section
            title="Budget"
            hint="Each strategy is tested as its own account. Lots are sized so one trade never risks more than its share, and returns and drawdowns are judged against this capital."
          >
            <div className="mb-3">
              <span className="form-label d-block">Lots per trade</span>
              <Toggle
                name="sizing"
                label="How lots are sized"
                value={budget.sizing ?? "budget"}
                options={[
                  { value: "budget", label: "Spend the budget" },
                  { value: "risk", label: "Limit the risk" },
                ]}
                onChange={(sizing) => setBudget({ sizing })}
              />
              <div className="form-text">
                {(budget.sizing ?? "budget") === "budget"
                  ? "As many lots as the money allows: a ₹100 option is ₹6,500 a Nifty lot, so ₹1,00,000 buys 15 lots. Money in open positions is not available again until they close."
                  : "As many lots as keep one trade's planned loss within the risk per trade, inside the account's delta, gamma, vega and stress limits."}
              </div>
            </div>
            <div className="row g-3">
              <div className="col-12 col-md-4">
                <Field id="capital" label="Capital, ₹" problem={visible.capital}>
                  <NumberInput
                    id="capital"
                    value={budget.capital}
                    step={10000}
                    invalid={!!visible.capital}
                    onChange={(value) => setBudget({ capital: value as number })}
                  />
                </Field>
              </div>
              {(budget.sizing ?? "budget") === "budget" ? (
                <div className="col-6 col-md-4">
                  <Field id="deploy" label="Spend per trade, % of capital" problem={visible.deploy_pct}>
                    <NumberInput
                      id="deploy"
                      value={budget.deploy_pct ?? 100}
                      step={5}
                      invalid={!!visible.deploy_pct}
                      onChange={(value) => setBudget({ deploy_pct: value as number })}
                    />
                  </Field>
                  <div className="form-text">
                    Up to ₹{Math.round((budget.capital * (budget.deploy_pct ?? 100)) / 100).toLocaleString("en-IN")} a trade
                  </div>
                </div>
              ) : (
              <div className="col-6 col-md-4">
                <Field
                  id="risk-per-trade"
                  label="Risk per trade, %"
                  problem={visible.risk_per_trade_pct}
                >
                  <NumberInput
                    id="risk-per-trade"
                    value={budget.risk_per_trade_pct}
                    step={0.5}
                    invalid={!!visible.risk_per_trade_pct}
                    onChange={(value) => setBudget({ risk_per_trade_pct: value as number })}
                  />
                </Field>
                {Number.isFinite(budget.capital * budget.risk_per_trade_pct) && (
                  <div className="form-text">
                    Up to ₹{Math.round((budget.capital * budget.risk_per_trade_pct) / 100).toLocaleString("en-IN")} at risk a trade
                  </div>
                )}
              </div>
              )}
              <div className="col-6 col-md-4">
                <Field id="max-open" label="Open positions at once" problem={visible.max_open}>
                  <NumberInput
                    id="max-open"
                    value={budget.max_open}
                    invalid={!!visible.max_open}
                    onChange={(value) => setBudget({ max_open: value as number })}
                  />
                </Field>
              </div>
            </div>
            <p className="form-text mb-0 mt-2">
              {(budget.sizing ?? "budget") === "budget"
                ? "Spending the budget can lose much more than a risk-sized trade: losses grow with the lots. Use the exit rules to cap them."
                : "Margin and the day's loss limit are shares of this capital too. The account's limits on delta, gamma, vega and stress losses still apply in rupees."}
            </p>
          </Section>

          <Section
            title="Indicators"
            hint="Pick the indicators this strategy uses. Every case's entry and exit rules choose from these, besides the candle's own prices."
            aside={<span className="badge text-bg-secondary">{picks.length}</span>}
          >
            {picks.length === 0 && (
              <p className="form-text mt-0">
                No indicators yet. Rules can still compare prices with each other or with a number.
              </p>
            )}
            <div className="vstack gap-2">
              {picks.map((pick, index) => {
                const key = pickKey(lab.technical, pick);
                const indicator = lab.technical.indicators.find((item) => item.name === pick.name);
                const used = useCount[key] ?? 0;
                return (
                  <div className="indicator-row" key={index}>
                    <div className="indicator-line">
                      <strong className="indicator-name">{pick.label?.trim() || indicator?.label || pick.name}</strong>
                      <div className="indicator-chips">
                        {pick.label?.trim() && (
                          <span className="indicator-chip">
                            Type: <b>{indicator?.label ?? pick.name}</b>
                          </span>
                        )}
                        <span className="indicator-chip">
                          Interval:{" "}
                          <b>{lab.technical.timeframes.find((t) => t.value === pick.timeframe)?.label ?? pick.timeframe}</b>
                        </span>
                        <span className="indicator-chip">
                          Chart: <b>{pick.candles === "heikin_ashi" ? "Heikin-Ashi" : "Candles"}</b>
                        </span>
                        {(indicator?.params ?? []).map((param) => (
                          <span className="indicator-chip" key={param.name}>
                            {param.label}: <b>{pick.params[param.name] ?? param.default}</b>
                          </span>
                        ))}
                        {indicator && indicator.fields.length > 0 && (
                          <span className="indicator-chip">
                            Lines: <b>{indicator.fields.map((field) => field.label).join(", ")}</b>
                          </span>
                        )}
                        <span className={`indicator-chip${used ? " is-used" : ""}`}>
                          {used ? `In ${used} rule${used === 1 ? "" : "s"}` : "Not in a rule yet"}
                        </span>
                      </div>
                      <div className="indicator-actions">
                        <button
                          type="button"
                          className="btn btn-sm btn-outline-primary"
                          aria-label={`Edit ${pickText(lab.technical, pick)}`}
                          onClick={() => setIndicatorForm({ editing: pick })}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-outline-danger"
                          disabled={used > 0}
                          title={used ? "Remove it from its rules first." : undefined}
                          aria-label={`Delete ${pickText(lab.technical, pick)}`}
                          onClick={() => setSpec({ ...spec, indicators: picks.filter((_, i) => i !== index) })}
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
            <button type="button" className="btn btn-primary btn-sm mt-3" onClick={() => setIndicatorForm({ editing: null })}>
              Add indicator
            </button>
            {indicatorForm && (
              <IndicatorForm
                // A fresh form each time it opens, never the last one's half-filled state.
                key={indicatorForm.editing ? pickKey(lab.technical, indicatorForm.editing) : "new"}
                catalog={lab.technical}
                picks={picks}
                editing={indicatorForm.editing}
                intraday={intraday}
                defaultFrame={(spec.decide_every ?? "day") === "day" ? "1d" : (spec.decide_every as TechTimeframe)}
                onSave={(pick) => {
                  if (indicatorForm.editing) replacePick(indicatorForm.editing, pick);
                  else addPick(pick);
                  setIndicatorForm(null);
                }}
                onClose={() => setIndicatorForm(null)}
              />
            )}
          </Section>

          <Section
            title="Features"
            hint="Optional ways to manage a trade. Each applies to every case."
            aside={<span className="badge text-bg-secondary">{featuresOn} on</span>}
          >
            <div className="feature-grid">
              <FeatureSwitch
                id="feature-trail"
                label="Trailing stop"
                hint="Once in profit, the stop follows the best P&L at this distance."
                checked={spec.exits.trail !== null && spec.exits.trail !== undefined}
                onChange={(on) => setExits({ trail: on ? UNIT_DEFAULTS[spec.exits.trail_unit ?? "premium"] / 2 : null })}
              >
                <Field id="trail" label="Distance" problem={visible.trail}>
                  <AmountInput
                    id="trail"
                    value={spec.exits.trail}
                    unit={spec.exits.trail_unit}
                    invalid={!!visible.trail}
                    onChange={(value, unit) => setExits({ trail: value, trail_unit: unit })}
                  />
                </Field>
              </FeatureSwitch>
              <FeatureSwitch
                id="feature-breakeven"
                label="Move stop to cost"
                hint="Once the gain reaches this, the stop moves to the entry price."
                checked={spec.exits.breakeven_at !== null && spec.exits.breakeven_at !== undefined}
                onChange={(on) =>
                  setExits({ breakeven_at: on ? UNIT_DEFAULTS[spec.exits.breakeven_unit ?? "premium"] / 2 : null })
                }
              >
                <Field id="breakeven" label="After a gain of" problem={visible.breakeven_at}>
                  <AmountInput
                    id="breakeven"
                    value={spec.exits.breakeven_at}
                    unit={spec.exits.breakeven_unit}
                    invalid={!!visible.breakeven_at}
                    onChange={(value, unit) => setExits({ breakeven_at: value, breakeven_unit: unit })}
                  />
                </Field>
              </FeatureSwitch>
              <FeatureSwitch
                id="feature-reenter"
                label="Re-entry"
                hint="Enter again the same day after a stop loss closes a trade."
                checked={spec.entry.reenter_after_stop ?? true}
                onChange={(on) => setEntry({ reenter_after_stop: on })}
              />
              <FeatureSwitch
                id="feature-reexecute"
                label="Re-execute"
                hint="Enter again the same day after a profit target closes a trade."
                checked={spec.entry.reenter_after_target ?? true}
                onChange={(on) => setEntry({ reenter_after_target: on })}
              />
              <FeatureSwitch
                id="feature-wait"
                label="Wait and trade"
                hint={
                  intraday
                    ? "When the entry rules hold, wait for the chosen options' premium to move before entering. A wait ends with the day."
                    : "Needs a decision every 5, 15 or 60 minutes, set under Basics."
                }
                disabled={!intraday}
                checked={spec.entry.wait_move !== null && spec.entry.wait_move !== undefined}
                onChange={(on) =>
                  setEntry(on ? { wait_move: 10, wait_unit: "pct", wait_direction: "up" } : { wait_move: null })
                }
              >
                <Field id="wait-move" label="Premium moves" problem={visible.wait_move}>
                  <div className="input-group">
                    <label className="visually-hidden" htmlFor="wait-direction">
                      Direction
                    </label>
                    <select
                      id="wait-direction"
                      className="form-select flex-grow-0 w-auto"
                      value={spec.entry.wait_direction ?? "up"}
                      onChange={(event) => setEntry({ wait_direction: event.target.value as "up" | "down" })}
                    >
                      <option value="up">Up</option>
                      <option value="down">Down</option>
                    </select>
                    <NumberInput
                      id="wait-move"
                      value={spec.entry.wait_move ?? null}
                      step={spec.entry.wait_unit === "points" ? 1 : 5}
                      invalid={!!visible.wait_move}
                      onChange={(value) => setEntry({ wait_move: value })}
                    />
                    <select
                      className="form-select flex-grow-0 w-auto"
                      aria-label="Wait unit"
                      value={spec.entry.wait_unit ?? "pct"}
                      onChange={(event) => setEntry({ wait_unit: event.target.value as LegUnit })}
                    >
                      <option value="pct">%</option>
                      <option value="points">pts</option>
                    </select>
                  </div>
                </Field>
              </FeatureSwitch>
            </div>
          </Section>

          <Section
            title="Cases"
            hint="Each case is a way into the strategy, with its own entry rules, exit rules and legs. The first case whose rules hold trades."
            aside={<span className="badge text-bg-secondary">{caseCount} of {MAX_CASES + 1}</span>}
          >
            <div className="case-bar">
              <div className="case-tabs" role="tablist" aria-label="Cases">
                {Array.from({ length: caseCount }, (_, index) => (
                  <button
                    key={index}
                    type="button"
                    role="tab"
                    id={`case-tab-${index}`}
                    aria-selected={index === active}
                    aria-controls="case-panel"
                    tabIndex={index === active ? 0 : -1}
                    className={`case-tab${index === active ? " is-active" : ""}${casesWithProblems.has(index) ? " has-problem" : ""}`}
                    onClick={() => setActiveCase(index)}
                    onKeyDown={(event) => {
                      const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
                      if (!step) return;
                      event.preventDefault();
                      const next = (index + step + caseCount) % caseCount;
                      setActiveCase(next);
                      document.getElementById(`case-tab-${next}`)?.focus();
                    }}
                  >
                    {index === 0 ? "Case 1" : cases[index - 1].name.trim() || `Case ${index + 1}`}
                  </button>
                ))}
              </div>
              <div className="d-flex flex-wrap gap-2">
                {cases.length < MAX_CASES && (
                  <>
                    <button type="button" className="btn btn-outline-secondary btn-sm" onClick={duplicateCase}>
                      Duplicate
                    </button>
                    <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => addCase(oppositeCase(spec))}>
                      Add opposite case
                    </button>
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      onClick={() =>
                        addCase({
                          name: `Case ${caseCount + 1}`,
                          bias: "neutral",
                          legs: [{ ...NEW_LEG }],
                          rules: [],
                          any_rules: [],
                        })
                      }
                    >
                      Add case
                    </button>
                  </>
                )}
              </div>
            </div>

            <div className="case-panel" role="tabpanel" id="case-panel" aria-labelledby={`case-tab-${active}`}>
              {active === 0 ? (
                <p className="form-text mt-0">
                  Case 1 is the strategy's own legs and rules. Its market view is set under Basics.
                </p>
              ) : (
                <div className="row g-2 align-items-end mb-2">
                  <div className="col-12 col-md-5">
                    <Field id={`case-${active - 1}-name`} label="Case name" problem={visible[`case-${active - 1}-name`]}>
                      <input
                        id={`case-${active - 1}-name`}
                        className={`form-control form-control-sm${visible[`case-${active - 1}-name`] ? " is-invalid" : ""}`}
                        maxLength={40}
                        value={cases[active - 1].name}
                        onChange={(event) => setCase(active - 1, { name: event.target.value })}
                      />
                    </Field>
                  </div>
                  <div className="col-12 col-md-5">
                    <span className="form-label d-block">Market view</span>
                    <Toggle
                      name={`case-${active - 1}-bias`}
                      label="Case market view"
                      value={cases[active - 1].bias}
                      options={BIASES}
                      onChange={(bias) => setCase(active - 1, { bias })}
                    />
                  </div>
                  <div className="col-12 col-md-2 text-md-end">
                    <button type="button" className="btn btn-sm btn-outline-danger" onClick={() => removeCase(active - 1)}>
                      Remove case
                    </button>
                  </div>
                </div>
              )}

              <div className="case-block case-entry">
                <div className="case-block-head">
                  <strong>Entry when</strong>
                  <small className="text-body-secondary">
                    {intraday
                      ? "Rules on the underlying, on 5-minute to monthly candles. A trade enters at the next check."
                      : "Rules on the underlying, on daily to monthly candles, read at the close. The trade enters at the next open."}
                  </small>
                </div>
                <span className="small fw-semibold d-block mb-1">All of these</span>
                <TechRulesEditor
                  key={`entry-${active}`}
                  picked={picks}
                  catalog={lab.technical}
                  rules={activeRules}
                  problems={visible}
                  intraday={intraday}
                  quickAdd={active === 0}
                  prefix={active === 0 ? "rule" : `case-${active - 1}-rule`}
                  onChange={(next) => (active === 0 ? setEntry({ rules: next }) : setCase(active - 1, { rules: next }))}
                />
                <span className="small fw-semibold d-block mt-3 mb-1">And any one of these</span>
                <TechRulesEditor
                  key={`any-${active}`}
                  picked={picks}
                  catalog={lab.technical}
                  rules={activeAnyRules}
                  problems={visible}
                  intraday={intraday}
                  prefix={active === 0 ? "any-rule" : `case-${active - 1}-any-rule`}
                  addLabel="Add an any-of rule"
                  onChange={(next) =>
                    active === 0 ? setEntry({ any_rules: next }) : setCase(active - 1, { any_rules: next })
                  }
                />
                <small className="form-text d-block">Empty lists add no condition. The shared rules under Enter when apply too.</small>
              </div>

              <div className="case-block case-exit">
                <div className="case-block-head">
                  <strong>Exit when</strong>
                  <small className="text-body-secondary">Close this case's trade when any of these holds, checked at each decision.</small>
                </div>
                {active > 0 && (
                  <div className="form-check form-switch mb-2">
                    <input
                      id={`case-${active - 1}-same-exits`}
                      className="form-check-input"
                      type="checkbox"
                      role="switch"
                      checked={cases[active - 1].exit_rules == null}
                      onChange={(event) => setCase(active - 1, { exit_rules: event.target.checked ? null : [] })}
                    />
                    <label className="form-check-label" htmlFor={`case-${active - 1}-same-exits`}>
                      Same exit rules as Case 1
                    </label>
                  </div>
                )}
                {active > 0 && cases[active - 1].exit_rules == null ? (
                  <small className="text-body-secondary">
                    {exitRules.length ? exitRules.map((rule) => ruleText(lab.technical, rule)).join("; or ") : "Case 1 has no exit rules."}
                  </small>
                ) : (
                  <TechRulesEditor
                    key={`exit-${active}`}
                  picked={picks}
                    catalog={lab.technical}
                    rules={activeExitRules}
                    problems={visible}
                    intraday={intraday}
                    prefix={active === 0 ? "exit-rule" : `case-${active - 1}-exit-rule`}
                    addLabel="Add an exit rule"
                    onChange={(next) =>
                      active === 0 ? setExits({ rules: next }) : setCase(active - 1, { exit_rules: next })
                    }
                  />
                )}
              </div>

              <div className="case-block">
                <div className="case-block-head">
                  <strong>Legs</strong>
                  <span className="badge text-bg-secondary">
                    {activeLegs.length} of {MAX_LEGS}
                  </span>
                </div>
                <LegsEditor
                  key={`legs-${active}`}
                  legs={activeLegs}
                  prefix={active === 0 ? "leg" : `case-${active - 1}-leg`}
                  problems={visible}
                  onChange={(legs) => (active === 0 ? setSpec({ ...spec, legs }) : setCase(active - 1, { legs }))}
                />
              </div>
            </div>
          </Section>

          <Section title="Enter when" hint="Shared by every case: each of these must hold as well as the case's own rules.">
            <div className="row g-3">
              <div className="col-12 col-md-6">
                <span className="form-label d-block">Trend</span>
                {TRENDS.map((trend) => (
                  <span key={trend}>
                    <input
                      id={`trend-${trend}`}
                      className="btn-check"
                      type="checkbox"
                      checked={spec.entry.trend.includes(trend)}
                      onChange={(event) =>
                        setEntry({
                          trend: event.target.checked
                            ? [...spec.entry.trend, trend]
                            : spec.entry.trend.filter((item) => item !== trend),
                        })
                      }
                    />
                    <label className="btn btn-outline-secondary btn-sm me-1 text-capitalize" htmlFor={`trend-${trend}`}>
                      {trend}
                    </label>
                  </span>
                ))}
                <div className="form-text">None selected means any trend.</div>
              </div>
              <div className="col-6 col-md-3">
                <Field id="iv-min" label="IV rank, min" problem={visible.iv_rank_min}>
                  <NumberInput id="iv-min" value={spec.entry.iv_rank_min} placeholder="Any" invalid={!!visible.iv_rank_min} onChange={(iv_rank_min) => setEntry({ iv_rank_min })} />
                </Field>
              </div>
              <div className="col-6 col-md-3">
                <Field id="iv-max" label="IV rank, max" problem={visible.iv_rank_max}>
                  <NumberInput id="iv-max" value={spec.entry.iv_rank_max} placeholder="Any" invalid={!!visible.iv_rank_max} onChange={(iv_rank_max) => setEntry({ iv_rank_max })} />
                </Field>
              </div>
              <div className="col-6 col-md-3">
                <Field id="dte-min" label="Days to expiry, min" problem={visible.dte_min}>
                  <NumberInput id="dte-min" value={spec.entry.dte_min} invalid={!!visible.dte_min} onChange={(value) => setEntry({ dte_min: value as number })} />
                </Field>
              </div>
              <div className="col-6 col-md-3">
                <Field id="dte-max" label="Days to expiry, max" problem={visible.dte_max}>
                  <NumberInput id="dte-max" value={spec.entry.dte_max} invalid={!!visible.dte_max} onChange={(value) => setEntry({ dte_max: value as number })} />
                </Field>
              </div>
              {intraday && (
                <>
                  <div className="col-6 col-md-3">
                    <Field id="time-from" label="Enter from" hint="Empty means 09:15." problem={visible.time_from}>
                      <input
                        id="time-from"
                        type="time"
                        className={`form-control${visible.time_from ? " is-invalid" : ""}`}
                        value={hhmm(spec.entry.time_from)}
                        onChange={(event) => setEntry({ time_from: event.target.value || null })}
                      />
                    </Field>
                  </div>
                  <div className="col-6 col-md-3">
                    <Field id="time-to" label="Enter until" hint="Empty means 15:25." problem={visible.time_to}>
                      <input
                        id="time-to"
                        type="time"
                        className={`form-control${visible.time_to ? " is-invalid" : ""}`}
                        value={hhmm(spec.entry.time_to)}
                        onChange={(event) => setEntry({ time_to: event.target.value || null })}
                      />
                    </Field>
                  </div>
                </>
              )}
            </div>

            <hr />
            <div className="d-flex justify-content-between align-items-center mb-2">
              <strong className="small">Market conditions</strong>
              <span className="badge text-bg-secondary">
                {conditions.length} of {MAX_CONDITIONS}
              </span>
            </div>
            <div className="vstack gap-2">
              {conditions.map((condition, index) => (
                <div className="row g-2 align-items-start" key={index}>
                  <div className="col-12 col-md-6">
                    <label className="visually-hidden" htmlFor={`condition-${index}-signal`}>
                      Signal
                    </label>
                    <select
                      id={`condition-${index}-signal`}
                      className="form-select"
                      value={condition.signal}
                      onChange={(event) => setCondition(index, { signal: event.target.value })}
                    >
                      {lab.signals.map((signal) => (
                        <option key={signal.name} value={signal.name}>
                          {signal.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="col-5 col-md-2">
                    <label className="visually-hidden" htmlFor={`condition-${index}-op`}>
                      Direction
                    </label>
                    <select
                      id={`condition-${index}-op`}
                      className="form-select"
                      value={condition.op}
                      onChange={(event) => setCondition(index, { op: event.target.value as LabCondition["op"] })}
                    >
                      <option value="above">Above</option>
                      <option value="below">Below</option>
                    </select>
                  </div>
                  <div className="col-4 col-md-2">
                    <label className="visually-hidden" htmlFor={`condition-${index}-value`}>
                      Level
                    </label>
                    <NumberInput
                      id={`condition-${index}-value`}
                      value={condition.value}
                      step={0.5}
                      invalid={!!visible[`condition-${index}`]}
                      onChange={(value) => setCondition(index, { value: value as number })}
                    />
                    {visible[`condition-${index}`] && (
                      <div className="invalid-feedback d-block">{visible[`condition-${index}`]}</div>
                    )}
                  </div>
                  <div className="col-3 col-md-2 text-end">
                    <button
                      type="button"
                      className="btn btn-link btn-sm text-danger"
                      onClick={() => setEntry({ conditions: conditions.filter((_, i) => i !== index) })}
                    >
                      Remove
                    </button>
                  </div>
                </div>
              ))}
            </div>
            {conditions.length < MAX_CONDITIONS && (
              <button
                type="button"
                className="btn btn-outline-primary btn-sm mt-2"
                onClick={() =>
                  setEntry({ conditions: [...conditions, { signal: lab.signals[0].name, op: "above", value: 0 }] })
                }
              >
                Add a condition
              </button>
            )}

          </Section>

          <Section title="Exit when" hint="Shared by every case: the first of these to happen closes the whole trade.">
            <div className="row g-3">
              <div className="col-12 col-md-4">
                <Field id="target" label="Take profit at" hint="0.5× premium is half of it; ₹ and points are per lot." problem={visible.target_frac}>
                  <AmountInput
                    id="target"
                    value={spec.exits.target_frac}
                    unit={spec.exits.target_unit}
                    invalid={!!visible.target_frac}
                    onChange={(value, unit) => setExits({ target_frac: value as number, target_unit: unit })}
                  />
                </Field>
              </div>
              <div className="col-12 col-md-4">
                <Field id="stop" label="Cut loss at" hint="A tighter stop fits the per-trade limit." problem={visible.stop_frac}>
                  <AmountInput
                    id="stop"
                    value={spec.exits.stop_frac}
                    unit={spec.exits.stop_unit}
                    invalid={!!visible.stop_frac}
                    onChange={(value, unit) => setExits({ stop_frac: value as number, stop_unit: unit })}
                  />
                </Field>
              </div>
              <div className="col-12 col-md-4">
                <Field id="exit-dte" label="Close before expiry" hint="At 15:00, this many days before." problem={visible.exit_dte}>
                  <div className="input-group">
                    <NumberInput id="exit-dte" value={spec.exits.exit_dte} invalid={!!visible.exit_dte} onChange={(value) => setExits({ exit_dte: value as number })} />
                    <span className="input-group-text">days</span>
                  </div>
                </Field>
              </div>
              {intraday && (
                <div className="col-12 col-md-4">
                  <Field
                    id="square-off"
                    label="Square off by"
                    hint="Closes every position by this time each day. Empty holds overnight."
                    problem={visible.square_off}
                  >
                    <input
                      id="square-off"
                      type="time"
                      className={`form-control${visible.square_off ? " is-invalid" : ""}`}
                      value={hhmm(spec.exits.square_off)}
                      onChange={(event) => setExits({ square_off: event.target.value || null })}
                    />
                  </Field>
                </div>
              )}
            </div>

            <hr />
            <strong className="small d-block mb-2">Daily limits</strong>
            <div className="row g-3">
              <div className="col-6 col-md-3">
                <Field id="daily-stop" label="Daily stop loss" hint="₹, all trades of this strategy that day." problem={visible.daily_stop}>
                  <NumberInput id="daily-stop" value={spec.exits.daily_stop ?? null} step={500} placeholder="Off" invalid={!!visible.daily_stop} onChange={(value) => setExits({ daily_stop: value })} />
                </Field>
              </div>
              <div className="col-6 col-md-3">
                <Field id="daily-target" label="Daily take profit" hint="₹; then no more trades that day." problem={visible.daily_target}>
                  <NumberInput id="daily-target" value={spec.exits.daily_target ?? null} step={500} placeholder="Off" invalid={!!visible.daily_target} onChange={(value) => setExits({ daily_target: value })} />
                </Field>
              </div>
              <div className="col-6 col-md-3">
                <Field id="max-trades" label="Max trades per day" hint="Empty means no limit." problem={visible.max_trades_per_day}>
                  <NumberInput id="max-trades" value={spec.entry.max_trades_per_day ?? null} placeholder="No limit" invalid={!!visible.max_trades_per_day} onChange={(value) => setEntry({ max_trades_per_day: value })} />
                </Field>
              </div>
            </div>
          </Section>
        </div>

        <div className="col-12 col-xl-4" hidden={!summaryOpen}>
          <div className="strategy-summary" id="strategy-summary">
          <aside className="card">
            <div className="card-header d-flex justify-content-between align-items-center">
              <h2 className="h6 mb-0">Summary</h2>
              <div className="d-flex align-items-center gap-2">
                <span className={`badge ${level.tone}`}>{level.label}</span>
                <button type="button" className="btn btn-link btn-sm p-0" onClick={() => setChartOpen(true)}>
                  Chart
                </button>
                <button type="button" className="btn btn-link btn-sm p-0" onClick={() => setMapOpen(true)}>
                  Map
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-outline-secondary summary-fold"
                  aria-label="Hide the summary"
                  title="Hide the summary"
                  onClick={() => toggleSummary(false)}
                >
                  ›
                </button>
              </div>
            </div>
            <ul className="list-group list-group-flush">
              {spec.legs.map((leg, index) => (
                <li className="list-group-item d-flex align-items-center gap-2" key={index}>
                  <span className={`badge ${leg.action === "buy" ? "text-bg-primary" : "text-bg-warning"}`}>
                    {leg.action === "buy" ? "Buy" : "Sell"}
                  </span>
                  {legText(leg)}
                </li>
              ))}
            </ul>
            <div className="card-body vstack gap-2">
              <div className="small">
                <div>
                  <span className="text-body-secondary">Enters</span> {spec.entry.dte_min}–{spec.entry.dte_max} days to expiry
                  {spec.entry.trend.length ? `, ${spec.entry.trend.join(" or ")} trend` : ""}
                  {conditions.length ? `, ${conditions.length} market condition${conditions.length > 1 ? "s" : ""}` : ""}
                </div>
                {rules.length > 0 && (
                  <ul className="mb-0 ps-3">
                    {rules.map((rule, index) => (
                      <li key={index}>{ruleText(lab.technical, rule)}</li>
                    ))}
                  </ul>
                )}
                {anyRules.length > 0 && (
                  <div>
                    <span className="text-body-secondary">And any of</span>
                    <ul className="mb-0 ps-3">
                      {anyRules.map((rule, index) => (
                        <li key={index}>{ruleText(lab.technical, rule)}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <div>
                  <span className="text-body-secondary">Exits</span> profit {amountText(spec.exits.target_frac, spec.exits.target_unit)}, loss{" "}
                  {amountText(spec.exits.stop_frac, spec.exits.stop_unit)}, {spec.exits.exit_dte}d before expiry
                  {spec.exits.square_off ? `, square off by ${hhmm(spec.exits.square_off)}` : ""}
                  {spec.exits.trail ? `, trailing ${amountText(spec.exits.trail, spec.exits.trail_unit)}` : ""}
                  {spec.exits.breakeven_at ? `, to cost after ${amountText(spec.exits.breakeven_at, spec.exits.breakeven_unit)}` : ""}
                </div>
                {exitRules.length > 0 && (
                  <ul className="mb-0 ps-3">
                    {exitRules.map((rule, index) => (
                      <li key={index}>Exit: {ruleText(lab.technical, rule)}</li>
                    ))}
                  </ul>
                )}
                {(spec.exits.daily_stop || spec.exits.daily_target || spec.entry.max_trades_per_day || spec.entry.reenter_after_stop === false) && (
                  <div>
                    <span className="text-body-secondary">Each day</span>{" "}
                    {[
                      spec.exits.daily_stop ? `stop at −₹${spec.exits.daily_stop.toLocaleString("en-IN")}` : "",
                      spec.exits.daily_target ? `stop at +₹${spec.exits.daily_target.toLocaleString("en-IN")}` : "",
                      spec.entry.max_trades_per_day ? `at most ${spec.entry.max_trades_per_day} trades` : "",
                      spec.entry.reenter_after_stop === false ? "no re-entry after a stop" : "",
                    ]
                      .filter(Boolean)
                      .join(", ")}
                  </div>
                )}
                <div>
                  <span className="text-body-secondary">Decides</span>{" "}
                  {DECISIONS.find((d) => d.value === (spec.decide_every ?? "day"))?.label.toLowerCase()}
                  {spec.entry.time_from || spec.entry.time_to
                    ? `, entering ${hhmm(spec.entry.time_from) || "09:15"}–${hhmm(spec.entry.time_to) || "15:25"}`
                    : ""}
                </div>
              </div>
              {sellsMoreThanBuys(spec) && (
                <div className="alert alert-warning py-2 mb-0 small">
                  This sells more options than it buys, so its risk is undefined. The risk limits will
                  usually block it.
                </div>
              )}
              {attempted && Object.keys(problems).length > 0 && (
                <div className="alert alert-danger py-2 mb-0 small">
                  Fix the {Object.keys(problems).length === 1 ? "highlighted field" : `${Object.keys(problems).length} highlighted fields`} to save.
                </div>
              )}
              {error && <div className="alert alert-danger py-2 mb-0 small">{error}</div>}
              <button type="button" className="btn btn-primary" disabled={busy} onClick={() => save(false)}>
                {busy ? "Saving…" : "Save strategy"}
              </button>
              <button
                type="button"
                className="btn btn-outline-primary"
                disabled={busy || !lab.history.ready}
                title={lab.history.ready ? undefined : "Testing needs more history"}
                onClick={() => save(true)}
              >
                Save and test
              </button>
              <a className="btn btn-link btn-sm" href={backHref}>
                Cancel
              </a>
              {!lab.history.ready && (
                <div className="form-text mt-0">
                  You can save now. Testing needs {lab.history.needed} sessions of history and{" "}
                  {lab.history.sessions} are stored.
                </div>
              )}
            </div>
          </aside>
          </div>
        </div>
      </div>
    </div>
    </FoldContext.Provider>
  );
}

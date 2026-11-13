import "bootstrap/dist/css/bootstrap.min.css";
import { IndexChart } from "./IndexChart";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  api,
  type ExitUnit,
  type LabCondition,
  type LabLeg,
  type LabOverview,
  type LabSpec,
  type TechRule,
} from "../api";
import { StrategyVisual } from "./StrategyVisual";
import { MAX_RULES, MINUTE_FRAMES, ruleProblems, ruleText, TechRulesEditor } from "./TechRules";

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
  };
}

function inRange(value: number | null, low: number, high: number): boolean {
  return value !== null && Number.isFinite(value) && value >= low && value <= high;
}

/** The server checks these too. Here they point at the exact field while editing. */
function findProblems(spec: LabSpec): Problems {
  const problems: Problems = {};
  if (!spec.name.trim()) problems.name = "Give the strategy a name.";
  spec.legs.forEach((leg, index) => {
    if (!inRange(leg.ratio, 1, 4) || !Number.isInteger(leg.ratio)) {
      problems[`leg-${index}-ratio`] = "Use a whole number from 1 to 4.";
    }
    if (leg.select === "delta" && !inRange(leg.value, 0.05, 0.95)) {
      problems[`leg-${index}-value`] = "Use a delta from 0.05 to 0.95.";
    }
    if (leg.select === "atm_offset" && (!inRange(leg.value, -20, 20) || !Number.isInteger(leg.value))) {
      problems[`leg-${index}-value`] = "Use a whole number from -20 to 20.";
    }
  });
  const seen = new Set<string>();
  spec.legs.forEach((leg, index) => {
    const key = [leg.option_type, leg.select, leg.value, leg.expiry].join("|");
    if (seen.has(key)) problems[`leg-${index}-value`] = "Another leg already uses this strike.";
    seen.add(key);
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
  if (!inRange(entry.dte_min, 1, 45)) problems.dte_min = "Use 1 to 45 days.";
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
  for (const key of ["daily_stop", "daily_target"] as const) {
    const value = exits[key];
    if (value !== null && value !== undefined && !(value > 0)) problems[key] = "Use a positive amount, or leave empty.";
  }
  const trades = entry.max_trades_per_day;
  if (trades !== null && trades !== undefined && !(Number.isInteger(trades) && trades >= 1 && trades <= 50)) {
    problems.max_trades_per_day = "Use 1 to 50, or leave empty.";
  }
  if (!inRange(exits.exit_dte, 0, 10)) problems.exit_dte = "Use 0 to 10 days.";
  if (!problems.dte_min && !problems.exit_dte && entry.dte_min <= exits.exit_dte) {
    problems.exit_dte = "Must be below the minimum days to expiry, or no trade can be entered.";
  }
  (entry.conditions ?? []).forEach((condition, index) => {
    if (!Number.isFinite(condition.value) || condition.value === null) {
      problems[`condition-${index}`] = "Enter a level.";
    }
  });
  return problems;
}

function legText(leg: LabLeg): string {
  const strike =
    leg.select === "delta" ? `delta ${leg.value}` : leg.value ? `ATM${leg.value > 0 ? "+" : ""}${leg.value}` : "ATM";
  const later = leg.expiry ? `, expiry +${leg.expiry}` : "";
  return `${leg.ratio} ${leg.option_type === "CE" ? "Call" : "Put"} ${strike}${later}`;
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

function Section({ title, hint, aside, children }: { title: string; hint?: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="card mb-3">
      <div className="card-header d-flex justify-content-between align-items-center gap-2">
        <div>
          <h2 className="h6 mb-0">{title}</h2>
          {hint && <small className="text-body-secondary">{hint}</small>}
        </div>
        {aside}
      </div>
      <div className="card-body">{children}</div>
    </section>
  );
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
  const intraday = (spec.decide_every ?? "day") !== "day";
  const problems = useMemo(() => {
    const found: Record<string, string> = {
      ...findProblems(spec),
      ...(lab ? ruleProblems(lab.technical, spec.entry.rules ?? []) : {}),
      ...(lab ? ruleProblems(lab.technical, spec.exits.rules ?? [], "exit-rule") : {}),
      ...(lab ? ruleProblems(lab.technical, spec.entry.any_rules ?? [], "any-rule") : {}),
    };
    const daily = (spec.decide_every ?? "day") === "day";
    for (const [prefix, list] of [
      ["rule", spec.entry.rules ?? []],
      ["any-rule", spec.entry.any_rules ?? []],
      ["exit-rule", spec.exits.rules ?? []],
    ] as const) {
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
            entry: { ...current.entry, time_from: null, time_to: null },
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
  const setLeg = (index: number, change: Partial<LabLeg>) =>
    setSpec((current) => ({
      ...current,
      legs: current.legs.map((leg, i) => (i === index ? { ...leg, ...change } : leg)),
    }));
  const setCondition = (index: number, change: Partial<LabCondition>) =>
    setEntry({ conditions: conditions.map((item, i) => (i === index ? { ...item, ...change } : item)) });

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
    if (Object.keys(problems).length) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await api.labSave({ ...spec, name: spec.name.trim() });
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
    <div className="bs-scope strategy-page" data-bs-theme={theme}>
      <nav aria-label="breadcrumb">
        <ol className="breadcrumb mb-2">
          <li className="breadcrumb-item">
            <a href="#lab">Strategy lab</a>
          </li>
          <li className="breadcrumb-item active" aria-current="page">
            {title}
          </li>
        </ol>
      </nav>

      <Section
        title="Chart"
        hint="The underlying with any indicators you add. Arrows mark candles where your entry rules held. Use + on an indicator to turn it into an entry rule."
      >
        <IndexChart
          catalog={lab.technical}
          symbols={lab.chart_symbols ?? lab.symbols}
          tradable={lab.symbols}
          draft={{ ...spec, name: spec.name.trim() || "Draft" }}
          rules={rules}
          anyRules={anyRules}
          onAddRule={
            rules.length < MAX_RULES ? (rule) => setEntry({ rules: [...rules, rule] }) : undefined
          }
        />
      </Section>

      <div className="row g-3">
        <div className="col-12 col-xl-8">
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

          <Section
            title="Indicators"
            hint="Entry signals on the underlying: indicators, candle patterns and price levels."
            aside={<span className="badge text-bg-secondary">{rules.length + anyRules.length} rules</span>}
          >
            <div className="d-flex justify-content-between align-items-center mb-1">
              <strong className="small">All of these must hold</strong>
              <span className="badge text-bg-secondary">
                {rules.length} of {MAX_RULES}
              </span>
            </div>
            <p className="form-text mt-0">
              {intraday
                ? "Indicators, candle patterns and price levels on the underlying, each on 5-minute to monthly candles. Minute candles count once finished; daily and longer ones include today so far. A trade enters at the next check."
                : "Indicators, candle patterns and price levels on the underlying, each on daily, weekly or monthly candles. Rules read candles up to the decision at the close, and the trade enters at the next open. For 5-minute to hourly candles, decide every 5, 15 or 60 minutes."}
            </p>
            <TechRulesEditor
              catalog={lab.technical}
              rules={rules}
              problems={visible}
              intraday={intraday}
              quickAdd
              onChange={(next) => setEntry({ rules: next })}
            />

            <div className="d-flex justify-content-between align-items-center mt-3 mb-1">
              <strong className="small">Any of these</strong>
              <span className="badge text-bg-secondary">
                {anyRules.length} of {MAX_RULES}
              </span>
            </div>
            <p className="form-text mt-0">
              At least one of these must hold too, for a setup that can start in more than one way. Leave empty if
              every rule above is enough.
            </p>
            <TechRulesEditor
              catalog={lab.technical}
              rules={anyRules}
              problems={visible}
              intraday={intraday}
              prefix="any-rule"
              addLabel="Add an any-of rule"
              onChange={(next) => setEntry({ any_rules: next })}
            />
            <p className="form-text mb-0 mt-3">
              Exit signals are under Exit when. Strategies without indicators enter on the other entry rules
              alone.
            </p>
          </Section>

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

          <Section title="Legs" hint="The options bought and sold when the strategy enters." aside={<span className="badge text-bg-secondary">{spec.legs.length} of {MAX_LEGS}</span>}>
            <div className="vstack gap-3">
              {spec.legs.map((leg, index) => (
                <div className="border rounded p-2" key={index}>
                  <div className="d-flex justify-content-between align-items-center mb-2">
                    <strong className="small">Leg {index + 1}</strong>
                    {spec.legs.length > 1 && (
                      <button
                        type="button"
                        className="btn btn-sm btn-link text-danger p-0"
                        onClick={() => setSpec({ ...spec, legs: spec.legs.filter((_, i) => i !== index) })}
                      >
                        Remove
                      </button>
                    )}
                  </div>
                  <div className="row g-2">
                    <div className="col-6 col-md-3">
                      <span className="form-label d-block">Action</span>
                      <Toggle
                        name={`leg-${index}-action`}
                        label="Action"
                        value={leg.action}
                        options={[
                          { value: "buy", label: "Buy" },
                          { value: "sell", label: "Sell" },
                        ]}
                        onChange={(action) => setLeg(index, { action })}
                      />
                    </div>
                    <div className="col-6 col-md-3">
                      <span className="form-label d-block">Type</span>
                      <Toggle
                        name={`leg-${index}-type`}
                        label="Option type"
                        value={leg.option_type}
                        options={[
                          { value: "CE", label: "Call" },
                          { value: "PE", label: "Put" },
                        ]}
                        onChange={(option_type) => setLeg(index, { option_type })}
                      />
                    </div>
                    <div className="col-4 col-md-2">
                      <Field id={`leg-${index}-ratio`} label="Qty" problem={visible[`leg-${index}-ratio`]}>
                        <NumberInput
                          id={`leg-${index}-ratio`}
                          value={leg.ratio}
                          invalid={!!visible[`leg-${index}-ratio`]}
                          onChange={(value) => setLeg(index, { ratio: value as number })}
                        />
                      </Field>
                    </div>
                    <div className="col-8 col-md-4">
                      <Field id={`leg-${index}-expiry`} label="Expiry">
                        <select
                          id={`leg-${index}-expiry`}
                          className="form-select"
                          value={leg.expiry}
                          onChange={(event) => setLeg(index, { expiry: Number(event.target.value) })}
                        >
                          <option value={0}>Entry expiry</option>
                          <option value={1}>Next expiry</option>
                          <option value={2}>Expiry after next</option>
                        </select>
                      </Field>
                    </div>
                    <div className="col-7 col-md-5">
                      <Field id={`leg-${index}-select`} label="Choose strike by">
                        <select
                          id={`leg-${index}-select`}
                          className="form-select"
                          value={leg.select}
                          onChange={(event) => {
                            const select = event.target.value as LabLeg["select"];
                            setLeg(index, { select, value: select === "delta" ? 0.3 : 0 });
                          }}
                        >
                          <option value="delta">Delta</option>
                          <option value="atm_offset">Strikes from ATM</option>
                        </select>
                      </Field>
                    </div>
                    <div className="col-5 col-md-3">
                      <Field
                        id={`leg-${index}-value`}
                        label={leg.select === "delta" ? "Delta" : "Strikes"}
                        problem={visible[`leg-${index}-value`]}
                      >
                        <NumberInput
                          id={`leg-${index}-value`}
                          value={leg.value}
                          step={leg.select === "delta" ? 0.05 : 1}
                          invalid={!!visible[`leg-${index}-value`]}
                          onChange={(value) => setLeg(index, { value: value as number })}
                        />
                      </Field>
                    </div>
                  </div>
                </div>
              ))}
            </div>
            {spec.legs.length < MAX_LEGS && (
              <button
                type="button"
                className="btn btn-outline-primary btn-sm mt-3"
                onClick={() => setSpec({ ...spec, legs: [...spec.legs, { ...NEW_LEG, value: 0.3 }] })}
              >
                Add a leg
              </button>
            )}
          </Section>

          <Section title="Enter when" hint="Every rule here must hold on the day.">
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

          <Section title="Exit when" hint="The first of these to happen closes the trade.">
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
              <div className="col-12 col-md-4">
                <Field id="trail" label="Trailing stop" hint="Once in profit, the stop follows the best P&L at this distance. Empty is off." problem={visible.trail}>
                  <AmountInput
                    id="trail"
                    optional
                    value={spec.exits.trail}
                    unit={spec.exits.trail_unit}
                    invalid={!!visible.trail}
                    onChange={(value, unit) => setExits({ trail: value, trail_unit: unit })}
                  />
                </Field>
              </div>
              <div className="col-12 col-md-4">
                <Field id="breakeven" label="Move stop to cost after" hint="Once the gain reaches this, the stop moves to the entry price. Empty is off." problem={visible.breakeven_at}>
                  <AmountInput
                    id="breakeven"
                    optional
                    value={spec.exits.breakeven_at}
                    unit={spec.exits.breakeven_unit}
                    invalid={!!visible.breakeven_at}
                    onChange={(value, unit) => setExits({ breakeven_at: value, breakeven_unit: unit })}
                  />
                </Field>
              </div>
            </div>

            <hr />
            <div className="d-flex justify-content-between align-items-center mb-1">
              <strong className="small">Exit rules</strong>
              <span className="badge text-bg-secondary">
                {exitRules.length} of {MAX_RULES}
              </span>
            </div>
            <p className="form-text mt-0">
              Close the trade when any of these holds, checked at each decision. For example, exit
              when the 5-minute close crosses below EMA 20.
            </p>
            <TechRulesEditor
              catalog={lab.technical}
              rules={exitRules}
              problems={visible}
              intraday={intraday}
              prefix="exit-rule"
              addLabel="Add an exit rule"
              onChange={(next) => setExits({ rules: next })}
            />

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
              <div className="col-6 col-md-3 d-flex align-items-end">
                <div className="form-check mb-2">
                  <input
                    id="reenter"
                    className="form-check-input"
                    type="checkbox"
                    checked={spec.entry.reenter_after_stop ?? true}
                    onChange={(event) => setEntry({ reenter_after_stop: event.target.checked })}
                  />
                  <label className="form-check-label" htmlFor="reenter">
                    Re-enter after a stop
                  </label>
                </div>
              </div>
            </div>
          </Section>
        </div>

        <div className="col-12 col-xl-4">
          <div className="strategy-summary">
          <section className="card mb-3">
            <div className="card-header">
              <h2 className="h6 mb-0">Strategy map</h2>
              <small className="text-body-secondary">Updates as you edit.</small>
            </div>
            <div className="card-body">
              <StrategyVisual spec={spec} catalog={lab?.technical ?? null} signals={lab?.signals} />
            </div>
          </section>
          <aside className="card">
            <div className="card-header d-flex justify-content-between align-items-center">
              <h2 className="h6 mb-0">Summary</h2>
              <span className={`badge ${level.tone}`}>{level.label}</span>
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
  );
}

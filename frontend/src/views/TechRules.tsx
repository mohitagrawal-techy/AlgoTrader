import type { CandleType, TechCatalog, TechOperand, TechRule, TechTimeframe } from "../api";

export const MAX_RULES = 8;
const MAX_BARS_AGO = 50;

type Indicator = TechCatalog["indicators"][number];

/** Ready-made rules for common setups. They are ordinary rules and can be edited after. */
export const PRESETS: { label: string; rule: TechRule }[] = [
  {
    label: "Close above EMA 20",
    rule: {
      type: "compare",
      timeframe: "1d",
      left: { kind: "indicator", name: "close" },
      op: "above",
      right: { kind: "indicator", name: "ema", params: { period: 20 } },
    },
  },
  {
    label: "EMA 9 crosses above EMA 21",
    rule: {
      type: "compare",
      timeframe: "1d",
      left: { kind: "indicator", name: "ema", params: { period: 9 } },
      op: "crosses_above",
      right: { kind: "indicator", name: "ema", params: { period: 21 } },
    },
  },
  {
    label: "RSI 14 below 30",
    rule: {
      type: "compare",
      timeframe: "1d",
      left: { kind: "indicator", name: "rsi", params: { period: 14 } },
      op: "below",
      right: { kind: "value", value: 30 },
    },
  },
  {
    label: "Breaks the 20-day high",
    rule: {
      type: "compare",
      timeframe: "1d",
      left: { kind: "indicator", name: "close" },
      op: "above",
      right: { kind: "indicator", name: "prior_high", params: { period: 20 } },
    },
  },
  {
    label: "Supertrend up",
    rule: {
      type: "compare",
      timeframe: "1d",
      left: { kind: "indicator", name: "supertrend", params: { period: 10, multiplier: 3 }, field: "direction" },
      op: "above",
      right: { kind: "value", value: 0 },
    },
  },
  {
    label: "MACD above signal",
    rule: {
      type: "compare",
      timeframe: "1d",
      left: { kind: "indicator", name: "macd", params: { fast: 12, slow: 26, signal: 9 }, field: "macd" },
      op: "above",
      right: { kind: "indicator", name: "macd", params: { fast: 12, slow: 26, signal: 9 }, field: "signal" },
    },
  },
  {
    label: "Weekly close above weekly EMA 20",
    rule: {
      type: "compare",
      timeframe: "1w",
      left: { kind: "indicator", name: "close" },
      op: "above",
      right: { kind: "indicator", name: "ema", params: { period: 20 } },
    },
  },
  {
    label: "Narrow range, 5 days under 2%",
    rule: {
      type: "compare",
      timeframe: "1d",
      left: { kind: "indicator", name: "range_pct", params: { period: 5 } },
      op: "below",
      right: { kind: "value", value: 2 },
    },
  },
  {
    label: "Heikin-Ashi candle green",
    rule: {
      type: "compare",
      timeframe: "1d",
      candles: "heikin_ashi",
      left: { kind: "indicator", name: "close" },
      op: "above",
      right: { kind: "indicator", name: "open" },
    },
  },
  {
    label: "CCI 20 above 100",
    rule: {
      type: "compare",
      timeframe: "1d",
      left: { kind: "indicator", name: "cci", params: { period: 20 } },
      op: "above",
      right: { kind: "value", value: 100 },
    },
  },
  {
    label: "Williams %R 14 below -80",
    rule: {
      type: "compare",
      timeframe: "1d",
      left: { kind: "indicator", name: "williams_r", params: { period: 14 } },
      op: "below",
      right: { kind: "value", value: -80 },
    },
  },
  {
    label: "Bullish engulfing",
    rule: { type: "pattern", timeframe: "1d", pattern: "bullish_engulfing", within: 1, present: true },
  },
  {
    label: "Bearish engulfing",
    rule: { type: "pattern", timeframe: "1d", pattern: "bearish_engulfing", within: 1, present: true },
  },
];

function indicatorOf(catalog: TechCatalog, name: string | null | undefined): Indicator | undefined {
  return catalog.indicators.find((item) => item.name === name);
}

/** An indicator operand with its default settings. */
function freshIndicator(catalog: TechCatalog, name: string): TechOperand {
  const indicator = indicatorOf(catalog, name);
  return {
    kind: "indicator",
    name,
    params: Object.fromEntries((indicator?.params ?? []).map((p) => [p.name, p.default])),
    field: indicator?.fields[0]?.value ?? null,
    bars_ago: 0,
  };
}

function operandText(catalog: TechCatalog, operand: TechOperand): string {
  if (operand.kind === "value") return String(operand.value ?? "?");
  const indicator = indicatorOf(catalog, operand.name);
  if (!indicator) return operand.name ?? "?";
  const params = indicator.params.map((p) => operand.params?.[p.name] ?? p.default).join(",");
  let text = indicator.label;
  if (params) text += ` (${params})`;
  const field = indicator.fields.find((f) => f.value === operand.field);
  if (field) text += ` ${field.label}`;
  if (operand.bars_ago) text += `, ${operand.bars_ago} candles ago`;
  return text;
}

export function ruleText(catalog: TechCatalog, rule: TechRule): string {
  let frame = catalog.timeframes.find((t) => t.value === rule.timeframe)?.label.toLowerCase() ?? rule.timeframe;
  if (rule.candles === "heikin_ashi") frame += " Heikin-Ashi";
  if (rule.type === "pattern") {
    const label = catalog.patterns.find((p) => p.name === rule.pattern)?.label.toLowerCase() ?? rule.pattern;
    const when = rule.within === 1 ? "on the latest candle" : `in the last ${rule.within} candles`;
    return `${rule.present ? "" : "No "}${frame} ${label} ${when}`.replace(/^./, (c) => c.toUpperCase());
  }
  const op = catalog.operators.find((o) => o.value === rule.op)?.label ?? rule.op;
  return `${frame[0].toUpperCase()}${frame.slice(1)}: ${operandText(catalog, rule.left)} ${op} ${operandText(catalog, rule.right)}`;
}

/** Problems keyed by field id, matching the editor's other checks. */
export function ruleProblems(
  catalog: TechCatalog,
  rules: TechRule[],
  prefix = "rule",
): Record<string, string> {
  const problems: Record<string, string> = {};
  const checkOperand = (id: string, operand: TechOperand) => {
    if (operand.kind === "value") {
      if (operand.value === null || operand.value === undefined || !Number.isFinite(operand.value)) {
        problems[id] = "Enter a number.";
      }
      return;
    }
    const indicator = indicatorOf(catalog, operand.name);
    for (const param of indicator?.params ?? []) {
      const value = operand.params?.[param.name];
      const whole = !param.whole || Number.isInteger(value);
      if (value === undefined || !Number.isFinite(value) || value < param.min || value > param.max || !whole) {
        problems[`${id}-${param.name}`] = `${param.label}: ${param.min} to ${param.max}${param.whole ? ", whole" : ""}.`;
      }
    }
    const ago = operand.bars_ago ?? 0;
    if (!Number.isInteger(ago) || ago < 0 || ago > MAX_BARS_AGO) problems[`${id}-ago`] = `0 to ${MAX_BARS_AGO}.`;
  };
  rules.forEach((rule, index) => {
    if (rule.type === "pattern") {
      if (!Number.isInteger(rule.within) || rule.within < 1 || rule.within > 20) problems[`${prefix}-${index}-within`] = "1 to 20.";
      return;
    }
    checkOperand(`${prefix}-${index}-left`, rule.left);
    checkOperand(`${prefix}-${index}-right`, rule.right);
    if (rule.left.kind === "value" && rule.right.kind === "value") {
      problems[`${prefix}-${index}-right`] = "Compare at least one indicator.";
    }
  });
  return problems;
}

function SmallNumber({
  id,
  label,
  value,
  step,
  invalid,
  onChange,
}: {
  id: string;
  label: string;
  value: number | null | undefined;
  step?: number;
  invalid?: boolean;
  onChange: (value: number | null) => void;
}) {
  return (
    <div className="tech-param">
      <label className="form-label small text-body-secondary mb-0" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        type="number"
        className={`form-control form-control-sm${invalid ? " is-invalid" : ""}`}
        value={value ?? ""}
        step={step ?? 1}
        onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))}
      />
    </div>
  );
}

function OperandEditor({
  id,
  catalog,
  operand,
  allowNumber,
  problems,
  onChange,
}: {
  id: string;
  catalog: TechCatalog;
  operand: TechOperand;
  allowNumber: boolean;
  problems: Record<string, string>;
  onChange: (operand: TechOperand) => void;
}) {
  const indicator = operand.kind === "indicator" ? indicatorOf(catalog, operand.name) : undefined;
  const choice = operand.kind === "value" ? "__number" : (operand.name ?? "");
  const message = Object.entries(problems).find(([key]) => key === id || key.startsWith(`${id}-`))?.[1];
  return (
    <div className="tech-operand">
      <select
        id={`${id}-name`}
        className="form-select form-select-sm"
        aria-label="Value to compare"
        value={choice}
        onChange={(event) =>
          onChange(
            event.target.value === "__number"
              ? { kind: "value", value: 0 }
              : freshIndicator(catalog, event.target.value),
          )
        }
      >
        {allowNumber && <option value="__number">A number</option>}
        {catalog.indicators.map((item) => (
          <option key={item.name} value={item.name}>
            {item.label}
          </option>
        ))}
      </select>
      <div className="tech-params">
        {operand.kind === "value" ? (
          <SmallNumber
            id={id}
            label="Number"
            value={operand.value}
            step={0.5}
            invalid={!!problems[id]}
            onChange={(value) => onChange({ ...operand, value })}
          />
        ) : (
          <>
            {indicator?.params.map((param) => (
              <SmallNumber
                key={param.name}
                id={`${id}-${param.name}`}
                label={param.label}
                value={operand.params?.[param.name]}
                step={param.whole ? 1 : 0.1}
                invalid={!!problems[`${id}-${param.name}`]}
                onChange={(value) =>
                  onChange({ ...operand, params: { ...operand.params, [param.name]: value as number } })
                }
              />
            ))}
            {indicator && indicator.fields.length > 0 && (
              <div className="tech-param">
                <label className="form-label small text-body-secondary mb-0" htmlFor={`${id}-field`}>
                  Line
                </label>
                <select
                  id={`${id}-field`}
                  className="form-select form-select-sm"
                  value={operand.field ?? ""}
                  onChange={(event) => onChange({ ...operand, field: event.target.value })}
                >
                  {indicator.fields.map((field) => (
                    <option key={field.value} value={field.value}>
                      {field.label}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <SmallNumber
              id={`${id}-ago`}
              label="Candles ago"
              value={operand.bars_ago ?? 0}
              invalid={!!problems[`${id}-ago`]}
              onChange={(value) => onChange({ ...operand, bars_ago: value ?? 0 })}
            />
          </>
        )}
      </div>
      {indicator?.description && <div className="form-text">{indicator.description}</div>}
      {message && <div className="invalid-feedback d-block">{message}</div>}
    </div>
  );
}

export const MINUTE_FRAMES = ["5m", "15m", "1h"];

export function TechRulesEditor({
  catalog,
  rules,
  problems,
  intraday,
  onChange,
  prefix = "rule",
  addLabel = "Add a rule",
}: {
  catalog: TechCatalog;
  rules: TechRule[];
  problems: Record<string, string>;
  /** Minute timeframes are offered only to strategies that decide during the day. */
  intraday: boolean;
  onChange: (rules: TechRule[]) => void;
  /** Field ids start with this, so entry and exit rules do not collide. */
  prefix?: string;
  addLabel?: string;
}) {
  const frames = catalog.timeframes.filter(
    (frame) => intraday || !MINUTE_FRAMES.includes(frame.value) || rules.some((r) => r.timeframe === frame.value),
  );
  const update = (index: number, rule: TechRule) => onChange(rules.map((item, i) => (i === index ? rule : item)));
  const setTimeframe = (index: number, timeframe: TechTimeframe) => update(index, { ...rules[index], timeframe });
  const candleTypes = catalog.candle_types ?? [];
  const setType = (index: number, type: TechRule["type"]) =>
    update(
      index,
      type === "pattern"
        ? {
            type,
            timeframe: rules[index].timeframe,
            candles: rules[index].candles,
            pattern: catalog.patterns[0].name,
            within: 1,
            present: true,
          }
        : {
            type,
            timeframe: rules[index].timeframe,
            candles: rules[index].candles,
            left: freshIndicator(catalog, "close"),
            op: "above",
            right: freshIndicator(catalog, "ema"),
          },
    );

  return (
    <div className="vstack gap-2">
      {rules.map((rule, index) => (
        <div className="tech-rule border rounded p-2" key={index}>
          <div className="d-flex flex-wrap gap-2 align-items-center">
            <select
              className="form-select form-select-sm w-auto"
              aria-label="Kind of rule"
              value={rule.type}
              onChange={(event) => setType(index, event.target.value as TechRule["type"])}
            >
              <option value="compare">Indicator or price</option>
              <option value="pattern">Candle pattern</option>
            </select>
            <select
              className="form-select form-select-sm w-auto"
              aria-label="Timeframe"
              value={rule.timeframe}
              onChange={(event) => setTimeframe(index, event.target.value as TechTimeframe)}
            >
              {frames.map((frame) => (
                <option key={frame.value} value={frame.value}>
                  {frame.label} candles
                </option>
              ))}
            </select>
            {candleTypes.length > 0 && (
              <select
                className="form-select form-select-sm w-auto"
                aria-label="Candle type"
                value={rule.candles ?? "normal"}
                onChange={(event) => update(index, { ...rule, candles: event.target.value as CandleType })}
              >
                {candleTypes.map((kind) => (
                  <option key={kind.value} value={kind.value}>
                    {kind.label}
                  </option>
                ))}
              </select>
            )}
            <button
              type="button"
              className="btn btn-link btn-sm text-danger ms-auto"
              onClick={() => onChange(rules.filter((_, i) => i !== index))}
            >
              Remove
            </button>
          </div>

          {rule.type === "compare" ? (
            <div className="tech-compare mt-2">
              <OperandEditor
                id={`${prefix}-${index}-left`}
                catalog={catalog}
                operand={rule.left}
                allowNumber={false}
                problems={problems}
                onChange={(left) => update(index, { ...rule, left })}
              />
              <select
                className="form-select form-select-sm tech-op"
                aria-label="Comparison"
                value={rule.op}
                onChange={(event) => update(index, { ...rule, op: event.target.value as typeof rule.op })}
              >
                {catalog.operators.map((op) => (
                  <option key={op.value} value={op.value}>
                    {op.label}
                  </option>
                ))}
              </select>
              <OperandEditor
                id={`${prefix}-${index}-right`}
                catalog={catalog}
                operand={rule.right}
                allowNumber
                problems={problems}
                onChange={(right) => update(index, { ...rule, right })}
              />
            </div>
          ) : (
            <div className="d-flex flex-wrap gap-2 align-items-end mt-2">
              <select
                className="form-select form-select-sm w-auto"
                aria-label="Seen or not seen"
                value={rule.present ? "yes" : "no"}
                onChange={(event) => update(index, { ...rule, present: event.target.value === "yes" })}
              >
                <option value="yes">Seen</option>
                <option value="no">Not seen</option>
              </select>
              <select
                className="form-select form-select-sm w-auto"
                aria-label="Candle pattern"
                value={rule.pattern}
                onChange={(event) => update(index, { ...rule, pattern: event.target.value })}
              >
                {(["bullish", "bearish", "neutral"] as const).map((bias) => (
                  <optgroup key={bias} label={`${bias[0].toUpperCase()}${bias.slice(1)}`}>
                    {catalog.patterns
                      .filter((p) => p.bias === bias)
                      .map((p) => (
                        <option key={p.name} value={p.name}>
                          {p.label}
                        </option>
                      ))}
                  </optgroup>
                ))}
              </select>
              <SmallNumber
                id={`${prefix}-${index}-within`}
                label="Within the last N candles"
                value={rule.within}
                invalid={!!problems[`${prefix}-${index}-within`]}
                onChange={(value) => update(index, { ...rule, within: value ?? 1 })}
              />
              <div className="form-text w-100">
                {catalog.patterns.find((p) => p.name === rule.pattern)?.description}
              </div>
            </div>
          )}
          <div className="small mt-2 text-body-secondary">
            Reads as: <span className="text-body">{ruleText(catalog, rule)}</span>
          </div>
        </div>
      ))}
      {rules.length < MAX_RULES && (
        <div className="d-flex flex-wrap gap-2 align-items-center">
          <button
            type="button"
            className="btn btn-outline-primary btn-sm"
            onClick={() =>
              onChange([
                ...rules,
                {
                  type: "compare",
                  timeframe: "1d",
                  left: freshIndicator(catalog, "close"),
                  op: "above",
                  right: freshIndicator(catalog, "ema"),
                },
              ])
            }
          >
            {addLabel}
          </button>
          <select
            className="form-select form-select-sm w-auto"
            aria-label="Add a ready-made rule"
            value=""
            onChange={(event) => {
              const preset = PRESETS[Number(event.target.value)];
              if (preset) onChange([...rules, structuredClone(preset.rule)]);
            }}
          >
            <option value="">Add a ready-made rule…</option>
            {PRESETS.map((preset, i) => (
              <option key={preset.label} value={i}>
                {preset.label}
              </option>
            ))}
          </select>
        </div>
      )}
    </div>
  );
}

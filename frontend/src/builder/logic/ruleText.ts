import type { TechCatalog, TechOperand, TechRule } from "../../api";
import { indicatorOf } from "./picks";

const MAX_BARS_AGO = 50;

export function operandText(
  catalog: TechCatalog,
  operand: TechOperand,
): string {
  if (operand.kind === "value") return String(operand.value ?? "?");
  const indicator = indicatorOf(catalog, operand.name);
  if (!indicator) return operand.name ?? "?";
  const params = indicator.params
    .map((p) => operand.params?.[p.name] ?? p.default)
    .join(",");
  let text = indicator.label;
  if (params) text += ` (${params})`;
  const field = indicator.fields.find((f) => f.value === operand.field);
  if (field) text += ` ${field.label}`;
  if (operand.bars_ago) text += `, ${operand.bars_ago} candles ago`;
  if (operand.timeframe) {
    const frame =
      catalog.timeframes.find((t) => t.value === operand.timeframe)?.label ??
      operand.timeframe;
    text += ` (${frame.toLowerCase()}${operand.candles === "heikin_ashi" ? " Heikin-Ashi" : ""})`;
  }
  return text;
}

/** A rule as a sentence: "Daily: Close above EMA (20)". */
export function ruleText(catalog: TechCatalog, rule: TechRule): string {
  let frame =
    catalog.timeframes
      .find((t) => t.value === rule.timeframe)
      ?.label.toLowerCase() ?? rule.timeframe;
  if (rule.candles === "heikin_ashi") frame += " Heikin-Ashi";
  if (rule.type === "pattern") {
    const label =
      catalog.patterns
        .find((p) => p.name === rule.pattern)
        ?.label.toLowerCase() ?? rule.pattern;
    const when =
      rule.within === 1
        ? "on the latest candle"
        : `in the last ${rule.within} candles`;
    return `${rule.present ? "" : "No "}${frame} ${label} ${when}`.replace(
      /^./,
      (c) => c.toUpperCase(),
    );
  }
  const op =
    catalog.operators.find((o) => o.value === rule.op)?.label ?? rule.op;
  const left = operandText(catalog, rule.left);
  return `${frame[0].toUpperCase()}${frame.slice(1)}: ${left} ${op} ${operandText(catalog, rule.right)}`;
}

/** The word joining rule `index` to the one before it in an entry list: "And", "Or", or
 * nothing for the first rule. */
export function joinWord(rules: TechRule[], index: number): string {
  if (index === 0) return "";
  return rules[index].join === "or" ? "Or" : "And";
}

/** Entry rules as one sentence, "and" binding before "or": "(A and B) or C". */
export function rulesText(catalog: TechCatalog, rules: TechRule[]): string {
  const groups: TechRule[][] = [];
  rules.forEach((rule, index) => {
    if (index === 0 || rule.join === "or") groups.push([]);
    groups[groups.length - 1].push(rule);
  });
  const texts = groups.map((group) =>
    group.map((rule) => ruleText(catalog, rule)).join(" and "),
  );
  if (groups.length < 2) return texts[0] ?? "";
  return texts
    .map((text, i) => (groups[i].length > 1 ? `(${text})` : text))
    .join(" or ");
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
      if (
        operand.value === null ||
        operand.value === undefined ||
        !Number.isFinite(operand.value)
      ) {
        problems[id] = "Enter a number.";
      }
      return;
    }
    const indicator = indicatorOf(catalog, operand.name);
    for (const param of indicator?.params ?? []) {
      const value = operand.params?.[param.name];
      const whole = !param.whole || Number.isInteger(value);
      if (
        value === undefined ||
        !Number.isFinite(value) ||
        value < param.min ||
        value > param.max ||
        !whole
      ) {
        const kind = param.whole ? ", whole" : "";
        problems[`${id}-${param.name}`] =
          `${param.label}: ${param.min} to ${param.max}${kind}.`;
      }
    }
    const ago = operand.bars_ago ?? 0;
    if (!Number.isInteger(ago) || ago < 0 || ago > MAX_BARS_AGO) {
      problems[`${id}-ago`] = `0 to ${MAX_BARS_AGO}.`;
    }
  };
  rules.forEach((rule, index) => {
    if (rule.type === "pattern") {
      if (
        !Number.isInteger(rule.within) ||
        rule.within < 1 ||
        rule.within > 20
      ) {
        problems[`${prefix}-${index}-within`] = "1 to 20.";
      }
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

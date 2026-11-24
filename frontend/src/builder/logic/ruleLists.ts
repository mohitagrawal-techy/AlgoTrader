import type {
  BaseTechRule,
  LabCase,
  LabSpec,
  TechOperand,
  TechRule,
} from "../../api";

/** The conditions a rule stands for: itself, or a signal's own conditions. */
export function baseRules(rule: TechRule): BaseTechRule[] {
  return rule.type === "signal" ? (rule.rules ?? []) : [rule];
}

/** The strategy with each signal condition carrying its signal's conditions, as the server
 * fills them in, for the chart and anything else that reads rules on their own. */
export function withSignalRules(spec: LabSpec): LabSpec {
  const byName = new Map(
    (spec.signals ?? []).map((signal) => [
      signal.name.trim().toLowerCase(),
      signal.rules,
    ]),
  );
  const fill = (rules: TechRule[]) =>
    rules.map((rule) =>
      rule.type === "signal"
        ? { ...rule, rules: byName.get(rule.name.trim().toLowerCase()) ?? [] }
        : rule,
    );
  return { ...mapRuleLists(spec, fill), signals: spec.signals };
}

/** Every rule list in a strategy, the strategy's own first, then each case's, then each
 * named signal's conditions. */
export function allRuleLists(spec: LabSpec): TechRule[][] {
  return [
    ...(spec.signals ?? []).map((signal) => signal.rules),
    spec.entry.rules ?? [],
    spec.entry.any_rules ?? [],
    spec.exits.rules ?? [],
    ...(spec.cases ?? []).flatMap((item) => [
      item.rules ?? [],
      item.any_rules ?? [],
      item.exit_rules ?? [],
    ]),
  ];
}

/** The same strategy with every rule list passed through `change`. */
export function mapRuleLists(
  spec: LabSpec,
  change: (rules: TechRule[]) => TechRule[],
): LabSpec {
  return {
    ...spec,
    entry: {
      ...spec.entry,
      rules: change(spec.entry.rules ?? []),
      any_rules: change(spec.entry.any_rules ?? []),
    },
    exits: { ...spec.exits, rules: change(spec.exits.rules ?? []) },
    signals: (spec.signals ?? []).map((signal) => ({
      ...signal,
      rules: change(signal.rules) as BaseTechRule[],
    })),
    cases: (spec.cases ?? []).map((item) => ({
      ...item,
      rules: change(item.rules ?? []),
      any_rules: change(item.any_rules ?? []),
      exit_rules:
        item.exit_rules == null ? item.exit_rules : change(item.exit_rules),
    })),
  };
}

/** Every rule list in a strategy with where it sits, for telling the user which rules use
 * something. A case that shares Case 1's exit rules has none of its own to list. */
export function labelledRuleLists(
  spec: LabSpec,
): { where: string; rules: TechRule[] }[] {
  const lists = [
    { where: "Case 1, entry", rules: spec.entry.rules ?? [] },
    { where: "Case 1, entry (any of)", rules: spec.entry.any_rules ?? [] },
    { where: "Case 1, exit", rules: spec.exits.rules ?? [] },
  ];
  for (const signal of spec.signals ?? [])
    lists.push({ where: `Signal "${signal.name}"`, rules: signal.rules });
  (spec.cases ?? []).forEach((item, index) => {
    const name = item.name.trim() || `Case ${index + 2}`;
    lists.push(
      { where: `${name}, entry`, rules: item.rules ?? [] },
      { where: `${name}, entry (any of)`, rules: item.any_rules ?? [] },
      { where: `${name}, exit`, rules: item.exit_rules ?? [] },
    );
  });
  return lists;
}

export function operandsOf(rule: TechRule): TechOperand[] {
  return rule.type === "compare" ? [rule.left, rule.right] : [];
}

/** The other side of a case: calls become puts, "above" becomes "below", highs become lows.
 * For a breakout that buys calls above a range, this is the put bought below it. */
export function mirrorRule(rule: TechRule): TechRule {
  if (rule.type !== "compare") return rule;
  const flip: Record<string, string> = {
    above: "below",
    below: "above",
    at_or_above: "at_or_below",
    at_or_below: "at_or_above",
    crosses_above: "crosses_below",
    crosses_below: "crosses_above",
  };
  const fields: Record<string, string> = {
    high: "low",
    low: "high",
    upper: "lower",
    lower: "upper",
  };
  const side = (operand: TechOperand): TechOperand =>
    operand.field && fields[operand.field]
      ? { ...operand, field: fields[operand.field] }
      : operand;
  return {
    ...rule,
    op: (flip[rule.op] ?? rule.op) as typeof rule.op,
    left: side(rule.left),
    right: side(rule.right),
  };
}

export function oppositeCase(spec: LabSpec): LabCase {
  const bias =
    spec.bias === "bullish"
      ? "bearish"
      : spec.bias === "bearish"
        ? "bullish"
        : "neutral";
  return {
    name: spec.legs.every((leg) => leg.option_type === "CE")
      ? "Put side"
      : "Call side",
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

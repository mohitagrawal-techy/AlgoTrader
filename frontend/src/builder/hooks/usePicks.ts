import { useMemo, type Dispatch, type SetStateAction } from "react";
import type {
  BaseTechRule,
  IndicatorPick,
  LabSpec,
  TechCatalog,
  TechOperand,
  TechRule,
} from "../../api";
import {
  normalPick,
  operandPick,
  patternPick,
  pickKey,
  PRICE_NAMES,
} from "../logic/picks";
import {
  allRuleLists,
  labelledRuleLists,
  mapRuleLists,
  operandsOf,
} from "../logic/ruleLists";

/** The strategy's indicators, and adding, changing and deleting them along with the rules
 * that read them. */
export function usePicks(
  spec: LabSpec,
  setSpec: Dispatch<SetStateAction<LabSpec>>,
  catalog: TechCatalog | null,
) {
  // Those picked, and any its rules already use (older strategies were saved without a
  // list), each once, with how many rules read each.
  const { picks, useCount } = useMemo(() => {
    const found: IndicatorPick[] = [];
    const counts: Record<string, number> = {};
    if (!catalog) return { picks: found, useCount: counts };
    const seen = new Set<string>();
    const add = (pick: IndicatorPick) => {
      const full = normalPick(catalog, pick.name, pick.params, pick);
      const key = pickKey(catalog, full);
      if (!seen.has(key)) {
        seen.add(key);
        found.push(full);
      }
      return key;
    };
    for (const pick of spec.indicators ?? []) add(pick);
    // Signal conditions read their signal, whose own conditions are listed with the rest.
    for (const rule of allRuleLists(spec).flat()) {
      if (rule.type === "signal") continue;
      if (rule.type === "pattern") {
        const key = add(patternPick(rule));
        counts[key] = (counts[key] ?? 0) + 1;
      }
      for (const operand of operandsOf(rule)) {
        if (
          operand.kind !== "indicator" ||
          !operand.name ||
          PRICE_NAMES.includes(operand.name)
        )
          continue;
        // An indicator a rule reads is on that rule's candles.
        const key = add(operandPick(operand, rule));
        counts[key] = (counts[key] ?? 0) + 1;
      }
    }
    return { picks: found, useCount: counts };
  }, [spec, catalog]);

  const keyOf = (pick: IndicatorPick) =>
    catalog ? pickKey(catalog, pick) : pick.name;
  /** Whether a rule reads this indicator, on its candles. */
  const ruleReads = (rule: TechRule, pick: IndicatorPick): boolean =>
    rule.type === "signal"
      ? false
      : rule.type === "pattern"
        ? keyOf(patternPick(rule)) === keyOf(pick)
        : [rule.left, rule.right].some(
            (operand) =>
              operand.kind === "indicator" &&
              operand.name === pick.name &&
              keyOf(operandPick(operand, rule)) === keyOf(pick),
          );

  const addPick = (pick: IndicatorPick) =>
    setSpec((current) => ({ ...current, indicators: [...picks, pick] }));

  /** The rules that read an indicator, with where each sits. */
  const rulesUsing = (pick: IndicatorPick) =>
    labelledRuleLists(spec).flatMap(({ where, rules }) =>
      rules
        .filter((rule) => ruleReads(rule, pick))
        .map((rule) => ({ where, rule })),
    );

  /** Removes an indicator, and with it every rule that reads it. */
  const deletePick = (pick: IndicatorPick) => {
    const key = keyOf(pick);
    setSpec((current) => ({
      ...mapRuleLists(current, (list) =>
        list.filter((rule) => !ruleReads(rule, pick)),
      ),
      indicators: picks.filter((other) => keyOf(other) !== key),
    }));
  };

  /** New settings, candles or name for a picked indicator, in the list and in every rule
   * that reads it: those rules move to its new candles. */
  const replacePick = (old: IndicatorPick, next: IndicatorPick) => {
    const oldKey = keyOf(old);
    const reads = (operand: TechOperand, rule: BaseTechRule) =>
      operand.kind === "indicator" &&
      operand.name === old.name &&
      keyOf(operandPick(operand, rule)) === oldKey;
    // A side that names its own candles moves to the new ones; one on the rule's candles moves
    // the rule.
    const swap = (operand: TechOperand, rule: BaseTechRule): TechOperand =>
      reads(operand, rule)
        ? {
            ...operand,
            params: { ...next.params },
            ...(operand.timeframe
              ? {
                  timeframe: next.timeframe ?? "1d",
                  candles: next.candles ?? "normal",
                }
              : {}),
          }
        : operand;
    const movesRule = (operand: TechOperand, rule: BaseTechRule) =>
      reads(operand, rule) && !operand.timeframe;
    const move = (rule: TechRule): TechRule =>
      rule.type === "signal"
        ? rule
        : rule.type === "pattern"
          ? keyOf(patternPick(rule)) === oldKey
            ? {
                ...rule,
                within: next.params.within ?? rule.within,
                timeframe: next.timeframe ?? "1d",
                candles: next.candles ?? "normal",
              }
            : rule
          : rule.type === "compare" &&
              (reads(rule.left, rule) || reads(rule.right, rule))
            ? {
                ...rule,
                left: swap(rule.left, rule),
                right: swap(rule.right, rule),
                ...(movesRule(rule.left, rule) || movesRule(rule.right, rule)
                  ? {
                      timeframe: next.timeframe ?? "1d",
                      candles: next.candles ?? "normal",
                    }
                  : {}),
              }
            : rule;
    setSpec((current) => ({
      ...mapRuleLists(current, (rules) => rules.map(move)),
      indicators: picks.map((pick) => (keyOf(pick) === oldKey ? next : pick)),
    }));
  };

  return { picks, useCount, addPick, replacePick, deletePick, rulesUsing };
}

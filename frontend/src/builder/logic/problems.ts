import type { LabSpec, TechCatalog, TechRule } from "../../api";
import { MINUTE_FRAMES } from "./picks";
import { baseRules, withSignalRules } from "./ruleLists";
import { ruleProblems } from "./ruleText";
import { hhmm } from "./spec";
import { findProblems, type Problems } from "./validation";

/** Every rule list with the prefix its fields' ids start with. */
function prefixedRuleLists(spec: LabSpec): [string, TechRule[]][] {
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
  (spec.signals ?? []).forEach((signal, index) =>
    lists.push([`signal-${index}-rule`, signal.rules]),
  );
  return lists;
}

/** Everything that stops the strategy saving: its fields, its rules and its times. */
export function allProblems(
  spec: LabSpec,
  catalog: TechCatalog | null,
): Problems {
  const found: Problems = { ...findProblems(spec) };
  const lists = prefixedRuleLists(withSignalRules(spec));
  const names = new Set(
    (spec.signals ?? []).map((signal) => signal.name.trim().toLowerCase()),
  );
  for (const [prefix, list] of lists)
    list.forEach((rule, index) => {
      if (rule.type === "signal" && !names.has(rule.name.trim().toLowerCase()))
        found[`${prefix}-${index}-left`] =
          `There is no signal named "${rule.name}" under Indicators.`;
    });
  const seen = new Set<string>();
  (spec.signals ?? []).forEach((signal, index) => {
    const key = signal.name.trim().toLowerCase();
    if (!key) found[`signal-${index}-name`] = "Name the signal.";
    else if (seen.has(key))
      found[`signal-${index}-name`] = "Another signal has this name.";
    else if (!signal.rules.length)
      found[`signal-${index}-name`] = "Add at least one condition.";
    seen.add(key);
  });
  if (catalog) {
    for (const [prefix, list] of lists)
      Object.assign(found, ruleProblems(catalog, list, prefix));
  }
  if ((spec.decide_every ?? "day") === "day") {
    for (const [prefix, list] of lists) {
      list.forEach((rule, index) => {
        if (baseRules(rule).some((r) => MINUTE_FRAMES.includes(r.timeframe))) {
          found[`${prefix}-${index}-left`] =
            "Minute candles need a decision every 5, 15 or 60 minutes.";
        }
      });
    }
  }
  const from = hhmm(spec.entry.time_from);
  const to = hhmm(spec.entry.time_to);
  const off = hhmm(spec.exits.square_off);
  const inSession = (t: string) => !t || (t >= "09:15" && t <= "15:25");
  if (!inSession(from)) found.time_from = "Use 09:15 to 15:25.";
  if (!inSession(to)) found.time_to = "Use 09:15 to 15:25.";
  if (from && to && from >= to) found.time_to = "Must be after the start.";
  if (off && !(off > "09:15" && off <= "15:25"))
    found.square_off = "Use 09:20 to 15:25.";
  return found;
}

/** The case tab a problem's field sits on: "case-N-..." on case N+1, the strategy's own on
 * Case 1, and null for fields outside the cases. */
export function tabOf(key: string): number | null {
  const match = /^case-(\d+)-/.exec(key);
  if (match) return Number(match[1]) + 1;
  return /^(leg|rule|any-rule|exit-rule)-/.test(key) ? 0 : null;
}

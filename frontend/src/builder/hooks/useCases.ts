import { useState, type Dispatch, type SetStateAction } from "react";
import type { LabCase, LabLeg, LabSpec, TechRule } from "../../api";
import { NEW_LEG } from "../logic/spec";

/** Case tabs: tab 0 is the strategy's own legs and rules, then each further case. The
 * setters change whichever tab is open. */
export function useCases(
  spec: LabSpec,
  setSpec: Dispatch<SetStateAction<LabSpec>>,
) {
  const [chosen, setActive] = useState(0);
  const cases: LabCase[] = spec.cases ?? [];
  const count = 1 + cases.length;
  const active = Math.min(chosen, cases.length);
  const own = active === 0;
  const item = own ? null : cases[active - 1];

  const setCase = (index: number, change: Partial<LabCase>) =>
    setSpec((current) => ({
      ...current,
      cases: (current.cases ?? []).map((one, i) =>
        i === index ? { ...one, ...change } : one,
      ),
    }));
  const setEntry = (change: Partial<LabSpec["entry"]>) =>
    setSpec((current) => ({
      ...current,
      entry: { ...current.entry, ...change },
    }));

  const rules: TechRule[] = item
    ? (item.rules ?? [])
    : (spec.entry.rules ?? []);
  const anyRules: TechRule[] = item
    ? (item.any_rules ?? [])
    : (spec.entry.any_rules ?? []);
  const exitRules: TechRule[] = item
    ? (item.exit_rules ?? [])
    : (spec.exits.rules ?? []);
  const legs: LabLeg[] = item ? item.legs : spec.legs;

  const setRules = (next: TechRule[]) =>
    own ? setEntry({ rules: next }) : setCase(active - 1, { rules: next });
  const setAnyRules = (next: TechRule[]) =>
    own
      ? setEntry({ any_rules: next })
      : setCase(active - 1, { any_rules: next });
  const setExitRules = (next: TechRule[]) =>
    own
      ? setSpec((current) => ({
          ...current,
          exits: { ...current.exits, rules: next },
        }))
      : setCase(active - 1, { exit_rules: next });
  const setLegs = (next: LabLeg[]) =>
    own
      ? setSpec((current) => ({ ...current, legs: next }))
      : setCase(active - 1, { legs: next });

  const addCase = (next: LabCase) => {
    setSpec((current) => ({
      ...current,
      cases: [...(current.cases ?? []), next],
    }));
    setActive(count);
  };
  const addBlankCase = () =>
    addCase({
      name: `Case ${count + 1}`,
      bias: "neutral",
      legs: [{ ...NEW_LEG }],
      rules: [],
      any_rules: [],
    });
  const duplicateCase = () => {
    const source: LabCase = item
      ? { ...item, name: `${item.name} copy`.slice(0, 40) }
      : {
          name: `Case ${count + 1}`,
          bias: spec.bias,
          legs: spec.legs,
          rules: spec.entry.rules ?? [],
          any_rules: spec.entry.any_rules ?? [],
          exit_rules: null,
        };
    addCase({ ...source, legs: source.legs.map((leg) => ({ ...leg })) });
  };
  const removeCase = (index: number) => {
    setSpec((current) => ({
      ...current,
      cases: (current.cases ?? []).filter((_, i) => i !== index),
    }));
    setActive(index);
  };

  return {
    cases,
    count,
    active,
    item,
    setActive,
    setCase,
    rules,
    anyRules,
    exitRules,
    legs,
    setRules,
    setAnyRules,
    setExitRules,
    setLegs,
    addCase,
    addBlankCase,
    duplicateCase,
    removeCase,
  };
}

export type CasesApi = ReturnType<typeof useCases>;

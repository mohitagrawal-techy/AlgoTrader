import type { Dispatch, SetStateAction } from "react";
import type { LabSpec, NamedSignal, TechRule } from "../../api";
import { allRuleLists, mapRuleLists } from "../logic/ruleLists";

const key = (name: string) => name.trim().toLowerCase();

/** The strategy's named signals, and adding, changing and deleting them along with the
 * conditions that read them. */
export function useSignals(
  spec: LabSpec,
  setSpec: Dispatch<SetStateAction<LabSpec>>,
) {
  const signals = spec.signals ?? [];
  const reads = (rule: TechRule, name: string) =>
    rule.type === "signal" && key(rule.name) === key(name);

  /** How many entry and exit conditions read a signal. */
  const useCount = (name: string) =>
    allRuleLists(spec)
      .flat()
      .filter((rule) => reads(rule, name)).length;

  const add = (signal: NamedSignal) =>
    setSpec((current) => ({
      ...current,
      signals: [...(current.signals ?? []), signal],
    }));

  /** New conditions or a new name for a signal; conditions reading it follow a rename. */
  const replace = (index: number, next: NamedSignal) =>
    setSpec((current) => {
      const old = (current.signals ?? [])[index];
      if (!old) return current;
      const renamed = mapRuleLists(current, (rules) =>
        rules.map((rule) =>
          reads(rule, old.name) && rule.type === "signal"
            ? { ...rule, name: next.name }
            : rule,
        ),
      );
      return {
        ...renamed,
        signals: (current.signals ?? []).map((signal, i) =>
          i === index ? next : signal,
        ),
      };
    });

  /** Removes a signal, and with it every condition that reads it. */
  const remove = (index: number) =>
    setSpec((current) => {
      const old = (current.signals ?? [])[index];
      if (!old) return current;
      const kept = mapRuleLists(current, (rules) => {
        const left = rules.filter((rule) => !reads(rule, old.name));
        // The first condition left joins nothing.
        if (left[0]?.join) left[0] = { ...left[0], join: undefined };
        return left;
      });
      return {
        ...kept,
        signals: (current.signals ?? []).filter((_, i) => i !== index),
      };
    });

  return { signals, useCount, add, replace, remove };
}

import { useMemo, useState } from "react";
import { api, type LabSpec, type TechTimeframe } from "../../api";
import { CANDLE_NAMES } from "../logic/picks";
import { allProblems, tabOf } from "../logic/problems";
import { allRuleLists, operandsOf } from "../logic/ruleLists";
import { blankSpec, DEFAULT_BUDGET, type Budget } from "../logic/spec";
import type { Problems } from "../logic/validation";
import { useCases } from "./useCases";
import { copyOf, useLab, type EditorRoute } from "./useLab";
import { usePicks } from "./usePicks";

/** A strategy switched to minute decisions reads its Current and Previous candle on those
 * minutes, while no rule reads them; a daily one drops its intraday-only settings. */
function withDecision(
  current: LabSpec,
  value: LabSpec["decide_every"],
): LabSpec {
  const frame: TechTimeframe = value === "day" || !value ? "1d" : value;
  const used = new Set(
    allRuleLists(current)
      .flat()
      .flatMap(operandsOf)
      .filter(
        (operand) =>
          operand.kind === "indicator" &&
          CANDLE_NAMES.includes(operand.name ?? ""),
      )
      .map((operand) => operand.name),
  );
  const indicators = (current.indicators ?? []).map((pick) =>
    CANDLE_NAMES.includes(pick.name) && !used.has(pick.name)
      ? { ...pick, timeframe: frame }
      : pick,
  );
  if (value !== "day") return { ...current, decide_every: value, indicators };
  return {
    ...current,
    decide_every: value,
    indicators,
    entry: {
      ...current.entry,
      time_from: null,
      time_to: null,
      wait_move: null,
    },
    exits: { ...current.exits, square_off: null },
  };
}

/** Everything the strategy builder edits and checks, and saving it. `onBlocked` hears of a
 * save stopped by problems, so the page can open the sections that hold them. */
export function useStrategyBuilder(route: EditorRoute, onBlocked: () => void) {
  const [spec, setSpec] = useState<LabSpec>(blankSpec);
  const { state, retry } = useLab(route, setSpec);
  const lab = state.status === "ready" ? state.lab : null;
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const picks = usePicks(spec, setSpec, lab?.technical ?? null);
  const cases = useCases(spec, setSpec);

  const problems = useMemo(
    () => allProblems(spec, lab?.technical ?? null),
    [spec, lab],
  );
  // The name is only marked once a save was tried, not while a new strategy is still blank.
  const visible: Problems = attempted ? problems : { ...problems, name: "" };
  const intraday = (spec.decide_every ?? "day") !== "day";
  const budget = spec.budget ?? DEFAULT_BUDGET;

  const edit = {
    set: (change: Partial<LabSpec>) =>
      setSpec((current) => ({ ...current, ...change })),
    entry: (change: Partial<LabSpec["entry"]>) =>
      setSpec((current) => ({
        ...current,
        entry: { ...current.entry, ...change },
      })),
    exits: (change: Partial<LabSpec["exits"]>) =>
      setSpec((current) => ({
        ...current,
        exits: { ...current.exits, ...change },
      })),
    budget: (change: Partial<Budget>) =>
      setSpec((current) => ({
        ...current,
        budget: { ...(current.budget ?? DEFAULT_BUDGET), ...change },
      })),
    decision: (value: LabSpec["decide_every"]) =>
      setSpec((current) => withDecision(current, value)),
    startFrom: (id: string) => {
      const row = lab?.strategies.find((item) => item.spec.id === id);
      setSpec(row ? copyOf(row.spec) : blankSpec());
    },
  };

  const save = async (thenTest: boolean) => {
    setAttempted(true);
    if (Object.keys(problems).length) {
      onBlocked();
      const tab = Object.keys(problems)
        .map(tabOf)
        .find((found) => found !== null);
      if (tab !== undefined && tab !== null) cases.setActive(tab);
      return;
    }
    setBusy(true);
    setSaveError(null);
    try {
      const saved = await api.labSave({
        ...spec,
        name: spec.name.trim(),
        indicators: picks.picks,
      });
      if (thenTest) await api.labBacktest([saved.id as string]);
      window.location.hash = `lab/view/${saved.id}`;
    } catch (reason) {
      setSaveError(
        reason instanceof Error ? reason.message : "Could not save.",
      );
      setBusy(false);
    }
  };

  return {
    state,
    retry,
    lab,
    spec,
    edit,
    budget,
    intraday,
    problems,
    visible,
    attempted,
    busy,
    saveError,
    save,
    picks,
    cases,
  };
}

export type Builder = ReturnType<typeof useStrategyBuilder>;
export type ReadyBuilder = Builder & { lab: NonNullable<Builder["lab"]> };

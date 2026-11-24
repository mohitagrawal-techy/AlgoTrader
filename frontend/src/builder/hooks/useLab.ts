import { useCallback, useEffect, useState } from "react";
import { api, type LabOverview, type LabSpec } from "../../api";

export type EditorRoute =
  { mode: "new" } | { mode: "edit" | "copy"; id: string };

export type LabState =
  | { status: "loading" }
  | { status: "failed"; message: string }
  | { status: "missing" }
  | { status: "ready"; lab: LabOverview };

/** A saved strategy as the start of a new one: no id, marked as a copy. */
export function copyOf(spec: LabSpec): LabSpec {
  return {
    ...spec,
    id: undefined,
    source: undefined,
    name: `${spec.name} copy`.slice(0, 60),
  };
}

/** Loads the strategy lab and, when editing or copying, the strategy itself. `onSpec`
 * receives that strategy once, as loaded. `retry` loads again after a failure. */
export function useLab(route: EditorRoute, onSpec: (spec: LabSpec) => void) {
  const [state, setState] = useState<LabState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => {
    setState({ status: "loading" });
    setAttempt((value) => value + 1);
  }, []);

  useEffect(() => {
    let active = true;
    api
      .lab()
      .then((lab) => {
        if (!active) return;
        if (route.mode !== "new") {
          const row = lab.strategies.find((item) => item.spec.id === route.id);
          if (!row) {
            setState({ status: "missing" });
            return;
          }
          onSpec(route.mode === "copy" ? copyOf(row.spec) : row.spec);
        }
        setState({ status: "ready", lab });
      })
      .catch((reason: unknown) => {
        if (!active) return;
        const message =
          // A network failure says only "Failed to fetch"; say what it means instead.
          reason instanceof Error && !(reason instanceof TypeError)
            ? reason.message
            : "The AlgoVerse server did not answer. Check that it is running, then try again.";
        setState({ status: "failed", message });
      });
    return () => {
      active = false;
    };
    // The route and the attempt decide what to load; onSpec only receives it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route, attempt]);

  return { state, retry };
}

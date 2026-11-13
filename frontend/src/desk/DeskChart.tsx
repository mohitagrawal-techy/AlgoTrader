import { useEffect, useState } from "react";
import { api, type LabOverview } from "../api";
import { IndexChart } from "../views/IndexChart";
import { Button } from "../components/ui";
import { DeskPanel, DeskSkeleton } from "./DeskPanel";

/** The index chart with its indicators, crosshair and replay, sized to the desk. */
export function DeskChart() {
  const [lab, setLab] = useState<LabOverview | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setFailed(false);
    api
      .lab()
      .then((data) => active && setLab(data))
      .catch(() => active && setFailed(true));
    return () => {
      active = false;
    };
  }, [attempt]);

  return (
    <DeskPanel title="Chart">
      {lab ? (
        <div className="p-2">
          <IndexChart
            catalog={lab.technical}
            symbols={lab.chart_symbols ?? lab.symbols}
            tradable={lab.symbols}
            rules={[]}
            anyRules={[]}
          />
        </div>
      ) : failed ? (
        <div className="flex flex-col items-center gap-3 px-3 py-10 text-[12.5px] text-ink-3">
          The chart could not load its settings from the server.
          <Button onClick={() => setAttempt((n) => n + 1)}>Try again</Button>
        </div>
      ) : (
        <DeskSkeleton rows={10} />
      )}
    </DeskPanel>
  );
}

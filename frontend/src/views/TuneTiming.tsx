import { useState } from "react";
import type { TuneNode, TuneSession } from "../api";
import { Segmented } from "../components/ui";
import { TimingGrid, timingFromDays } from "./TimingGrid";

type Period = "validate" | "design";

/** When one version made and lost money: by weekday and hour of entry, on the unseen or
 * the design sessions. Shows the version chosen in the charts or tree, else the best. */
export function TuneTiming({
  session,
  selectedId,
}: {
  session: TuneSession;
  selectedId: string | null;
}) {
  const [period, setPeriod] = useState<Period>("validate");
  const node: TuneNode | undefined =
    (selectedId ? session.nodes[selectedId] : undefined) ??
    session.nodes[session.best_id];
  if (!node) return null;
  const saved =
    period === "validate" ? node.validate_timing : node.design_timing;
  // Older sessions kept only the unseen daily P&L, so they show weekdays alone.
  const daily = (node.validate_curve ?? []).map((point, i, all) => ({
    day: point.day,
    pnl: point.pnl - (i ? all[i - 1].pnl : 0),
  }));
  const cells = saved ?? (period === "validate" ? timingFromDays(daily) : []);
  const name =
    node.depth === 0
      ? "the start version"
      : node.id === session.best_id
        ? `the best version (${node.label})`
        : node.label;
  return (
    <section className="tune-timing" aria-label="When it worked">
      <h3 className="tune-chart-title tune-chart-title-row">
        <span>
          When it worked
          <small className="secondary"> {name}</small>
        </span>
        <Segmented
          label="Sessions"
          value={period}
          options={[
            { value: "validate", label: "Unseen sessions" },
            { value: "design", label: "Design sessions" },
          ]}
          onChange={setPeriod}
        />
      </h3>
      {saved || period === "validate" ? (
        <>
          <TimingGrid cells={cells} unit={saved ? "trade" : "day"} />
          {!saved && (
            <p className="secondary">
              This session was saved before entry times were kept, so it shows
              weekdays from daily P&amp;L. New tuning runs add the hour of
              entry.
            </p>
          )}
        </>
      ) : (
        <p className="secondary">
          This session was saved before design-session timing was kept. New
          tuning runs include it.
        </p>
      )}
    </section>
  );
}

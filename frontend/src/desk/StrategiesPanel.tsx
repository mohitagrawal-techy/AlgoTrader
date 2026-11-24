import type { ForwardOverview, ForwardRun } from "../api";
import { fraction, signedInr } from "../format";
import { Pnl, cx } from "../components/ui";
import { DeskEmpty, DeskPanel, DeskSkeleton } from "./DeskPanel";

const STATE: Record<string, { label: string; dot: string }> = {
  active: { label: "Running", dot: "bg-good" },
  halted: { label: "Halted", dot: "bg-critical" },
  ended: { label: "Ended", dot: "bg-ink-3" },
};

function State({ run }: { run: ForwardRun }) {
  const state = STATE[run.status] ?? { label: run.status, dot: "bg-ink-3" };
  return (
    <span className="inline-flex items-center gap-1.5 text-ink-2">
      <span aria-hidden className={cx("size-1.5 rounded-full", state.dot)} />
      {state.label}
    </span>
  );
}

/** Locked configurations paper-trading on live data, running ones first. */
export function StrategiesPanel({
  forward,
}: {
  forward: ForwardOverview | undefined;
}) {
  const runs = [...(forward?.runs ?? [])].sort(
    (a, b) => Number(b.status === "active") - Number(a.status === "active"),
  );
  return (
    <DeskPanel
      title="Strategies"
      aside={
        <a
          href="#forward"
          className="text-accent-ink no-underline hover:underline"
        >
          Forward tests
        </a>
      }
    >
      {forward === undefined ? (
        <DeskSkeleton />
      ) : runs.length === 0 ? (
        <DeskEmpty>
          No forward test is running. Start one from Forward test.
        </DeskEmpty>
      ) : (
        <table className="w-full border-collapse text-[12.5px]">
          <thead className="sticky top-0 bg-surface text-[11px] text-ink-3">
            <tr className="border-b border-line text-left">
              <th className="px-3 py-1.5 font-medium">Strategy</th>
              <th className="px-2 py-1.5 font-medium">State</th>
              <th className="px-2 py-1.5 text-right font-medium">Trades</th>
              <th className="px-2 py-1.5 text-right font-medium">Win</th>
              <th className="px-2 py-1.5 text-right font-medium">Open</th>
              <th className="px-3 py-1.5 text-right font-medium">
                Net P&amp;L
              </th>
            </tr>
          </thead>
          <tbody className="font-mono tabular-nums">
            {runs.map((run) => (
              <tr key={run.id} className="border-b border-grid hover:bg-hover">
                <td className="px-3 py-1.5 font-sans">
                  <a
                    href="#forward"
                    className="text-ink no-underline hover:underline"
                  >
                    {run.name}
                  </a>
                  <span className="ml-1.5 text-[11px] text-ink-3">
                    v{run.version}
                  </span>
                </td>
                <td className="px-2 py-1.5 font-sans">
                  <State run={run} />
                </td>
                <td className="px-2 py-1.5 text-right">
                  {run.stats.trades ?? 0}
                </td>
                <td className="px-2 py-1.5 text-right text-ink-2">
                  {run.stats.win_rate === undefined
                    ? "–"
                    : fraction(run.stats.win_rate, 0)}
                </td>
                <td className="px-2 py-1.5 text-right">{run.live_positions}</td>
                <td className="px-3 py-1.5 text-right">
                  <Pnl value={run.stats.net_pnl ?? 0}>
                    {signedInr(run.stats.net_pnl ?? 0)}
                  </Pnl>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </DeskPanel>
  );
}

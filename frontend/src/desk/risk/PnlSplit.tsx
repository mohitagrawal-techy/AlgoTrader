import { useState } from "react";
import type { Portfolio, Position } from "../../api";
import { inrCompact, signedInr } from "../../format";
import { cx, Segmented } from "../../components/ui";
import { DeskEmpty, DeskPanel } from "../DeskPanel";

type GroupBy = "symbol" | "strategy";

const istDay = (iso: string) =>
  new Date(iso).toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });

interface Row {
  name: string;
  open: number;
  closed: number;
}

/** P&L of live positions plus trades closed today, grouped one way or the other. */
export function splitPnl(
  portfolio: Portfolio,
  groupBy: GroupBy,
  now = new Date(),
): Row[] {
  const today = istDay(now.toISOString());
  const rows = new Map<string, Row>();
  const add = (position: Position, open: number, closed: number) => {
    const name =
      groupBy === "symbol"
        ? position.symbol
        : position.strategy.replaceAll("_", " ");
    const row = rows.get(name) ?? { name, open: 0, closed: 0 };
    row.open += open;
    row.closed += closed;
    rows.set(name, row);
  };
  for (const position of portfolio.live)
    add(position, position.unrealized_pnl - position.entry_costs, 0);
  for (const position of portfolio.closed)
    if (position.closed_at && istDay(position.closed_at) === today)
      add(position, 0, position.realized_pnl ?? 0);
  return [...rows.values()].sort(
    (a, b) => Math.abs(b.open + b.closed) - Math.abs(a.open + a.closed),
  );
}

function Bar({ value, scale }: { value: number; scale: number }) {
  const width = `${Math.min(Math.abs(value) / scale, 1) * 50}%`;
  return (
    <div aria-hidden className="relative h-2 bg-surface-2">
      <span className="absolute inset-y-0 left-1/2 w-px bg-axis" />
      <span
        className={cx(
          "absolute inset-y-0 transition-[width] duration-150",
          value < 0 ? "bg-down" : "bg-up",
        )}
        style={value < 0 ? { right: "50%", width } : { left: "50%", width }}
      />
    </div>
  );
}

export function PnlSplit({ portfolio }: { portfolio: Portfolio }) {
  const [groupBy, setGroupBy] = useState<GroupBy>("symbol");
  const rows = splitPnl(portfolio, groupBy);
  const scale = Math.max(
    ...rows.map((row) => Math.abs(row.open + row.closed)),
    1,
  );
  return (
    <DeskPanel
      title="P&L split"
      aside={
        <Segmented
          label="Group P&L by"
          value={groupBy}
          onChange={setGroupBy}
          className="[&_span]:h-6 [&_span]:px-2 [&_span]:text-[11.5px]"
          options={[
            { value: "symbol", label: "Underlying" },
            { value: "strategy", label: "Strategy" },
          ]}
        />
      }
    >
      {rows.length ? (
        <>
          <ul className="m-0 list-none pl-0">
            {rows.map((row) => {
              const total = row.open + row.closed;
              return (
                <li
                  key={row.name}
                  className="grid grid-cols-[minmax(0,7rem)_minmax(0,1fr)_5.5rem] items-center gap-3 border-b border-grid px-3 py-2 last:border-b-0"
                >
                  <span
                    className="truncate text-[12.5px] capitalize text-ink"
                    title={row.name}
                  >
                    {row.name}
                  </span>
                  <Bar value={total} scale={scale} />
                  <span
                    className={cx(
                      "text-right font-mono text-[12px] tabular-nums",
                      total < 0
                        ? "text-down"
                        : total > 0
                          ? "text-up"
                          : "text-ink-3",
                    )}
                    title={`Open ${signedInr(row.open)}, closed today ${signedInr(row.closed)}`}
                  >
                    {total === 0
                      ? "0"
                      : `${total > 0 ? "+" : ""}${inrCompact(total)}`}
                  </span>
                </li>
              );
            })}
          </ul>
          <p className="m-0 border-t border-line px-3 py-2 text-[11.5px] text-ink-3">
            Open positions after entry costs, plus trades closed today.
          </p>
        </>
      ) : (
        <DeskEmpty>Nothing is open and nothing has closed today.</DeskEmpty>
      )}
    </DeskPanel>
  );
}

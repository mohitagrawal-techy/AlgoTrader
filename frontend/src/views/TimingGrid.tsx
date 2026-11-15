import type { CSSProperties } from "react";
import type { TimingCell } from "../api";
import { fraction, signedInr } from "../format";

export type { TimingCell } from "../api";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** The same grid the tuner saves, built from a backtest's trades. */
export function timingFromTrades(
  trades: { opened_at?: string | null; opened?: string; net_pnl: number }[],
): TimingCell[] {
  const cells = new Map<string, TimingCell>();
  for (const trade of trades) {
    const stamp = trade.opened_at ?? trade.opened;
    if (!stamp) continue;
    // Stamps carry IST, so the date and hour in the text are already the market's.
    const day =
      (new Date(`${stamp.slice(0, 10)}T00:00:00Z`).getUTCDay() + 6) % 7;
    const hour = stamp.length > 10 ? Number(stamp.slice(11, 13)) : -1;
    if (!Number.isFinite(day) || !Number.isFinite(hour)) continue;
    const key = `${day}:${hour}`;
    const cell = cells.get(key) ?? { day, hour, trades: 0, wins: 0, pnl: 0 };
    cell.trades += 1;
    cell.wins += trade.net_pnl > 0 ? 1 : 0;
    cell.pnl += trade.net_pnl;
    cells.set(key, cell);
  }
  return [...cells.values()];
}

/** Daily P&L by weekday only, for runs saved before entry times were kept. */
export function timingFromDays(
  days: { day: string; pnl: number }[],
): TimingCell[] {
  return timingFromTrades(
    days.map((d) => ({ opened: d.day, net_pnl: d.pnl })),
  ).map((cell) => ({ ...cell, hour: -1 }));
}

function hourLabel(hour: number): string {
  if (hour < 0) return "Any time";
  const start = hour === 9 ? "09:15" : `${String(hour).padStart(2, "0")}:00`;
  return `${start}–${String(hour + 1).padStart(2, "0")}:00`;
}

function compact(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "+";
  if (abs >= 1e5) return `${sign}₹${(abs / 1e5).toFixed(1)}L`;
  if (abs >= 1e3) return `${sign}₹${Math.round(abs / 1e3)}k`;
  return `${sign}₹${Math.round(abs)}`;
}

function sum(cells: TimingCell[]): TimingCell {
  return cells.reduce(
    (all, c) => ({
      ...all,
      trades: all.trades + c.trades,
      wins: all.wins + c.wins,
      pnl: all.pnl + c.pnl,
    }),
    { day: 0, hour: 0, trades: 0, wins: 0, pnl: 0 },
  );
}

function Cell({
  cell,
  scale,
  total,
  unit,
}: {
  cell: TimingCell | undefined;
  scale: number;
  total?: boolean;
  unit: string;
}) {
  if (!cell || cell.trades === 0) {
    return <td className="timing-cell empty">·</td>;
  }
  const strength = Math.min(Math.abs(cell.pnl) / scale, 1);
  const style = {
    "--timing-strength": `${Math.round(8 + strength * 52)}%`,
  } as CSSProperties;
  return (
    <td
      className={`timing-cell ${cell.pnl >= 0 ? "up" : "down"}${total ? " total" : ""}`}
      style={style}
      title={`${cell.trades} ${unit}${cell.trades === 1 ? "" : "s"}, ${fraction(cell.wins / cell.trades, 0)} won, net ${signedInr(cell.pnl)}`}
    >
      <span className="timing-pnl">{compact(cell.pnl)}</span>
      <span className="timing-count">
        {cell.trades} · {fraction(cell.wins / cell.trades, 0)}
      </span>
    </td>
  );
}

/** Net P&L by weekday and hour of entry, shaded green for profit and red for loss, with
 * each day's and each hour's total. */
export function TimingGrid({
  cells,
  unit = "trade",
}: {
  cells: TimingCell[];
  /** What each count is: "trade", or "day" for daily P&L. */
  unit?: string;
}) {
  if (!cells.length) {
    return <p className="secondary">No trades to group.</p>;
  }
  const days = [...new Set(cells.map((c) => c.day))].sort((a, b) => a - b);
  const hours = [...new Set(cells.map((c) => c.hour))].sort((a, b) => a - b);
  const at = (day: number, hour: number) =>
    cells.find((c) => c.day === day && c.hour === hour);
  const dayTotals = days.map((d) => sum(cells.filter((c) => c.day === d)));
  const hourTotals = hours.map((h) => sum(cells.filter((c) => c.hour === h)));
  const scale = Math.max(...cells.map((c) => Math.abs(c.pnl)), 1);
  const totalScale = Math.max(
    ...[...dayTotals, ...hourTotals].map((c) => Math.abs(c.pnl)),
    1,
  );
  const showHours = hours.length > 1 || hours[0] !== -1;
  const best = [...cells].sort((a, b) => b.pnl - a.pnl)[0];
  const worst = [...cells].sort((a, b) => a.pnl - b.pnl)[0];
  return (
    <div className="timing">
      <div className="timing-scroll">
        <table className="timing-table">
          <thead>
            <tr>
              <th scope="col">Day</th>
              {showHours &&
                hours.map((h) => (
                  <th scope="col" key={h} title={`Entered ${hourLabel(h)}`}>
                    {h < 0 ? "Any time" : hourLabel(h).split("–")[0]}
                  </th>
                ))}
              <th scope="col">{showHours ? "Day total" : "Net P&L"}</th>
            </tr>
          </thead>
          <tbody>
            {days.map((d, i) => (
              <tr key={d}>
                <th scope="row">{DAYS[d]}</th>
                {showHours &&
                  hours.map((h) => (
                    <Cell key={h} cell={at(d, h)} scale={scale} unit={unit} />
                  ))}
                <Cell
                  cell={dayTotals[i]}
                  scale={totalScale}
                  total
                  unit={unit}
                />
              </tr>
            ))}
          </tbody>
          {showHours && (
            <tfoot>
              <tr>
                <th scope="row">Hour total</th>
                {hourTotals.map((cell, i) => (
                  <Cell
                    key={hours[i]}
                    cell={cell}
                    scale={totalScale}
                    total
                    unit={unit}
                  />
                ))}
                <Cell cell={sum(cells)} scale={totalScale} total unit={unit} />
              </tr>
            </tfoot>
          )}
        </table>
      </div>
      <p className="secondary timing-note">
        Each cell: net P&amp;L, then {unit}s · share won.
        {best && best.pnl > 0 && (
          <>
            {" "}
            Best: {DAYS[best.day]}
            {showHours ? ` ${hourLabel(best.hour)}` : ""} ({signedInr(best.pnl)}
            ).
          </>
        )}
        {worst && worst.pnl < 0 && (
          <>
            {" "}
            Worst: {DAYS[worst.day]}
            {showHours ? ` ${hourLabel(worst.hour)}` : ""} (
            {signedInr(worst.pnl)}).
          </>
        )}{" "}
        Small counts are noise; look for whole rows or columns.
      </p>
    </div>
  );
}

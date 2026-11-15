import type { CSSProperties } from "react";
import { signedInr } from "../format";

type Period = "day" | "week" | "month";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"];
const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

function compact(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "+";
  if (abs >= 1e7) return `${sign}₹${(abs / 1e7).toFixed(1)}Cr`;
  if (abs >= 1e5) return `${sign}₹${(abs / 1e5).toFixed(1)}L`;
  if (abs >= 1e3) return `${sign}₹${Math.round(abs / 1e3)}k`;
  return `${sign}₹${Math.round(abs)}`;
}

/** Green for profit, red for loss, stronger for larger amounts against `scale`. */
function shade(value: number, scale: number): CSSProperties {
  const strength = Math.min(Math.abs(value) / (scale || 1), 1);
  return {
    "--heat-strength": `${Math.round(10 + strength * 60)}%`,
  } as CSSProperties;
}

/** The amount that gets full colour: the 90th-percentile size, so one huge day does not wash
 * out every other. Larger amounts simply stay at full colour. */
function colourScale(values: number[]): number {
  const sizes = values
    .map(Math.abs)
    .filter((v) => v > 0)
    .sort((a, b) => a - b);
  if (!sizes.length) return 1;
  return sizes[Math.min(sizes.length - 1, Math.floor(sizes.length * 0.9))];
}

function tone(value: number): string {
  return value > 0 ? "up" : value < 0 ? "down" : "flat";
}

function dayOf(day: string): Date {
  return new Date(`${day}T00:00:00Z`);
}

function iso(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/** Monday of the week a date falls in. */
function monday(day: string): Date {
  const date = dayOf(day);
  date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7));
  return date;
}

function Legend({ scale }: { scale: number }) {
  return (
    <div className="pnl-heat-legend" aria-hidden>
      <span>Loss {compact(-scale)} or more</span>
      {[-1, -0.5, -0.15, 0.15, 0.5, 1].map((step) => (
        <i
          key={step}
          className={`pnl-heat-swatch ${step < 0 ? "down" : "up"}`}
          style={shade(step * scale, scale)}
        />
      ))}
      <span>Profit {compact(scale)} or more</span>
    </div>
  );
}

/** Each trading day as a square: weekdays down the side, weeks across, months on top. */
function Calendar({ daily }: { daily: [string, number][] }) {
  const values = new Map(daily);
  const scale = colourScale(daily.map(([, v]) => v));
  const first = monday(daily[0][0]);
  const last = monday(daily[daily.length - 1][0]);
  const weeks: Date[] = [];
  for (
    const week = new Date(first);
    week <= last;
    week.setUTCDate(week.getUTCDate() + 7)
  ) {
    weeks.push(new Date(week));
  }
  return (
    <div className="pnl-heat">
      <div className="pnl-heat-scroll">
        <table className="pnl-calendar">
          <thead>
            <tr>
              <th aria-hidden />
              {weeks.map((week, i) => {
                const month = week.getUTCMonth();
                const starts = i === 0 || weeks[i - 1].getUTCMonth() !== month;
                return (
                  <th key={iso(week)} scope="col">
                    {starts ? MONTHS[month] : ""}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {DAYS.map((name, d) => (
              <tr key={name}>
                <th scope="row">{name}</th>
                {weeks.map((week) => {
                  const date = new Date(week);
                  date.setUTCDate(date.getUTCDate() + d);
                  const key = iso(date);
                  const value = values.get(key);
                  if (value === undefined) {
                    return <td key={key} className="pnl-day none" />;
                  }
                  const label = `${date.toLocaleDateString("en-IN", {
                    day: "numeric",
                    month: "short",
                    year: "numeric",
                    timeZone: "UTC",
                  })}: ${signedInr(value)}`;
                  return (
                    <td
                      key={key}
                      className={`pnl-day ${tone(value)}`}
                      style={shade(value, scale)}
                      title={label}
                      aria-label={label}
                    />
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Legend scale={scale} />
    </div>
  );
}

function Cell({
  value,
  scale,
  title,
}: {
  value: number | undefined;
  scale: number;
  title: string;
}) {
  if (value === undefined) return <td className="pnl-cell none">·</td>;
  return (
    <td
      className={`pnl-cell ${tone(value)}`}
      style={shade(value, scale)}
      title={title}
    >
      {compact(value)}
    </td>
  );
}

/** Weeks by the month their Monday falls in: months down the side, and across, the week of
 * the month that Monday starts (the 1st to 7th is week 1). */
function Weeks({ daily }: { daily: [string, number][] }) {
  const weeks = new Map<string, number>();
  for (const [day, pnl] of daily) {
    const key = iso(monday(day));
    weeks.set(key, (weeks.get(key) ?? 0) + pnl);
  }
  const rows = new Map<string, [string, number][]>();
  for (const [week, pnl] of weeks) {
    const month = week.slice(0, 7);
    rows.set(month, [...(rows.get(month) ?? []), [week, pnl]]);
  }
  const months = [...rows.keys()].sort();
  const scale = colourScale([...weeks.values()]);
  const totals = months.map((m) =>
    rows.get(m)!.reduce((sum, [, v]) => sum + v, 0),
  );
  const totalScale = Math.max(...totals.map(Math.abs), 1);
  return (
    <div className="pnl-heat">
      <div className="pnl-heat-scroll">
        <table className="pnl-grid">
          <thead>
            <tr>
              <th scope="col">Month</th>
              {[1, 2, 3, 4, 5].map((n) => (
                <th scope="col" key={n}>
                  Week {n}
                </th>
              ))}
              <th scope="col">Month total</th>
            </tr>
          </thead>
          <tbody>
            {months.map((month, i) => {
              const list = rows
                .get(month)!
                .sort(([a], [b]) => a.localeCompare(b));
              const [year, number] = month.split("-");
              return (
                <tr key={month}>
                  <th scope="row">
                    {MONTHS[Number(number) - 1]} {year.slice(2)}
                  </th>
                  {[1, 2, 3, 4, 5].map((n) => {
                    // A week sits in the column of its Monday: days 1-7 are week 1.
                    const found = list.find(
                      ([week]) =>
                        Math.floor((Number(week.slice(8)) - 1) / 7) + 1 === n,
                    );
                    return (
                      <Cell
                        key={n}
                        value={found?.[1]}
                        scale={scale}
                        title={
                          found
                            ? `Week of ${found[0]}: ${signedInr(found[1])}`
                            : ""
                        }
                      />
                    );
                  })}
                  <Cell
                    value={totals[i]}
                    scale={totalScale}
                    title={`${MONTHS[Number(number) - 1]} ${year}: ${signedInr(totals[i])}`}
                  />
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <Legend scale={scale} />
    </div>
  );
}

/** Monthly P&L: years down the side, January to December across, and each year's total. */
function Months({ daily }: { daily: [string, number][] }) {
  const months = new Map<string, number>();
  for (const [day, pnl] of daily) {
    const key = day.slice(0, 7);
    months.set(key, (months.get(key) ?? 0) + pnl);
  }
  const years = [
    ...new Set([...months.keys()].map((k) => k.slice(0, 4))),
  ].sort();
  const scale = colourScale([...months.values()]);
  const totals = years.map((y) =>
    [...months.entries()]
      .filter(([k]) => k.startsWith(y))
      .reduce((sum, [, v]) => sum + v, 0),
  );
  const totalScale = Math.max(...totals.map(Math.abs), 1);
  return (
    <div className="pnl-heat">
      <div className="pnl-heat-scroll">
        <table className="pnl-grid">
          <thead>
            <tr>
              <th scope="col">Year</th>
              {MONTHS.map((m) => (
                <th scope="col" key={m}>
                  {m}
                </th>
              ))}
              <th scope="col">Year total</th>
            </tr>
          </thead>
          <tbody>
            {years.map((year, i) => (
              <tr key={year}>
                <th scope="row">{year}</th>
                {MONTHS.map((m, n) => {
                  const key = `${year}-${String(n + 1).padStart(2, "0")}`;
                  const value = months.get(key);
                  return (
                    <Cell
                      key={m}
                      value={value}
                      scale={scale}
                      title={
                        value === undefined
                          ? ""
                          : `${m} ${year}: ${signedInr(value)}`
                      }
                    />
                  );
                })}
                <Cell
                  value={totals[i]}
                  scale={totalScale}
                  title={`${year}: ${signedInr(totals[i])}`}
                />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Legend scale={scale} />
    </div>
  );
}

/** Profit and loss by day, week or month as a heat map. `daily` is [date, P&L], by date. */
export function PnlHeatmap({
  daily,
  period,
}: {
  daily: [string, number][];
  period: Period;
}) {
  const sorted = [...daily].sort(([a], [b]) => a.localeCompare(b));
  if (!sorted.length) return null;
  if (period === "day") return <Calendar daily={sorted} />;
  if (period === "week") return <Weeks daily={sorted} />;
  return <Months daily={sorted} />;
}

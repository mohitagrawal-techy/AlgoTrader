import { forwardRef, useEffect, useMemo, useState } from "react";
import { api, type StrikeHistory, type StrikePoint } from "../api";
import { LineChart, timeTicks } from "../charts";
import { compact, num, shortDate, timeIst } from "../format";
import { Empty, Panel, Select, Status, Tile } from "../ui";

const PRICE = "var(--series-1)";
const OI = "var(--series-3)";
const IV = "var(--series-5)";
// The latest day keeps updating while it is open in the dashboard.
const REFRESH_MS = 60_000;

export type OptionSide = "CE" | "PE";

export interface StrikeChoice {
  strike: number;
  type: OptionSide;
}

function clock(ms: number): string {
  return timeIst(new Date(ms).toISOString());
}

function line(points: StrikePoint[], pick: (p: StrikePoint) => number | null) {
  return points.map((p) => ({ x: p.t, y: pick(p) }));
}

function first(points: StrikePoint[], pick: (p: StrikePoint) => number | null) {
  for (const p of points) if (pick(p) != null) return pick(p);
  return null;
}

function last(points: StrikePoint[], pick: (p: StrikePoint) => number | null) {
  for (let i = points.length - 1; i >= 0; i -= 1) if (pick(points[i]) != null) return pick(points[i]);
  return null;
}

function change(from: number | null, to: number | null) {
  if (from == null || to == null) return null;
  return to - from;
}

function signed(value: number | null, format: (v: number) => string) {
  if (value == null) return "–";
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${format(Math.abs(value))}`;
}

function useStrikeHistory(symbol: string, expiry: string, choice: StrikeChoice, day: string) {
  const [state, setState] = useState<{ data: StrikeHistory | null; error: string | null; loading: boolean }>({
    data: null,
    error: null,
    loading: true,
  });
  useEffect(() => {
    let active = true;
    const load = (quiet: boolean) => {
      if (!quiet) setState((s) => ({ ...s, loading: true }));
      api
        .marketStrike(symbol, expiry, choice.strike, choice.type, day || undefined)
        .then((data) => active && setState({ data, error: null, loading: false }))
        .catch((err: Error) => active && setState((s) => ({ ...s, error: err.message, loading: false })));
    };
    load(false);
    // Only the newest day changes, so older days are loaded once.
    const timer = day ? undefined : window.setInterval(() => {
      if (document.visibilityState === "visible") load(true);
    }, REFRESH_MS);
    return () => {
      active = false;
      if (timer) window.clearInterval(timer);
    };
  }, [symbol, expiry, choice.strike, choice.type, day]);
  return state;
}

export const StrikeChart = forwardRef<
  HTMLDivElement,
  {
    symbol: string;
    expiry: string;
    /** Strikes in the chain table, used until the saved list for the day arrives. */
    fallbackStrikes: number[];
    choice: StrikeChoice;
    onChoose: (choice: StrikeChoice) => void;
  }
>(function StrikeChart({ symbol, expiry, fallbackStrikes, choice, onChoose }, ref) {
  // An empty day means the newest saved day.
  const [day, setDay] = useState("");
  const { data, error, loading } = useStrikeHistory(symbol, expiry, choice, day);
  const shown = data && data.strike === choice.strike && data.option_type === choice.type ? data : null;
  const points = shown?.points ?? [];

  const price = useMemo(
    () => [
      { name: "LTP", color: PRICE, points: line(points, (p) => p.ltp) },
    ],
    [points],
  );
  const oi = useMemo(() => [{ name: "OI", color: OI, points: line(points, (p) => p.oi) }], [points]);
  const iv = useMemo(
    () => [{ name: "IV", color: IV, points: line(points, (p) => (p.iv == null ? null : p.iv * 100)) }],
    [points],
  );

  const open = first(points, (p) => p.ltp);
  const close = last(points, (p) => p.ltp);
  const prices = points.map((p) => p.ltp).filter((v): v is number => v != null);
  const oiChange = change(first(points, (p) => p.oi), last(points, (p) => p.oi));
  const ivNow = last(points, (p) => p.iv);
  const ivChange = change(first(points, (p) => p.iv), ivNow);
  const days = shown?.days ?? data?.days ?? [];
  // Every strike saved for this expiry that day, which reaches beyond the chain table.
  const saved = data?.strikes?.length ? data.strikes : fallbackStrikes;
  const strikes = saved.includes(choice.strike)
    ? saved
    : [...saved, choice.strike].sort((a, b) => a - b);
  const name = `${symbol} ${num(choice.strike)} ${choice.type} · ${shortDate(expiry)}`;

  return (
    <div ref={ref}>
      <Panel
        title={`Strike chart, ${name}`}
        aside={
          <span className="chart-controls">
            <Select
              label="Strike"
              value={String(choice.strike)}
              onChange={(value) => onChoose({ ...choice, strike: Number(value) })}
              options={strikes.map((s) => ({ value: String(s), label: num(s) }))}
            />
            <Select
              label="Side"
              value={choice.type}
              onChange={(type) => onChoose({ ...choice, type })}
              options={[
                { value: "CE", label: "Call" },
                { value: "PE", label: "Put" },
              ]}
            />
            <Select
              label="Day"
              value={day}
              onChange={setDay}
              options={[
                { value: "", label: days[0] ? `${shortDate(days[0])} (latest)` : "Latest" },
                ...days.slice(1).map((d) => ({ value: d, label: shortDate(d) })),
              ]}
            />
            {shown?.source === "ticks" ? (
              <Status tone="good">Live feed, 1 min</Status>
            ) : shown?.source === "snapshots" ? (
              <span className="muted">Snapshots, every 5 min</span>
            ) : null}
          </span>
        }
      >
        {error && !shown ? (
          <Empty title="Could not load this strike">{error}</Empty>
        ) : !shown ? (
          <Empty title={loading ? "Loading…" : "No data"} />
        ) : points.length === 0 ? (
          <Empty title="Nothing saved for this strike on this day">
            Pick another day, or a strike that was listed then.
          </Empty>
        ) : (
          <>
            <div className="tiles tiles-4">
              <Tile
                label="Last price"
                value={num(close, 2)}
                hint={`${signed(change(open, close), (v) => num(v, 2))} since ${clock(points[0].t)}`}
              />
              <Tile
                label="Day range"
                value={prices.length ? `${num(Math.min(...prices), 2)} – ${num(Math.max(...prices), 2)}` : "–"}
                hint={`Open ${num(open, 2)}`}
              />
              <Tile
                label="Open interest"
                value={compact(last(points, (p) => p.oi))}
                hint={`${signed(oiChange, compact)} in the day`}
              />
              <Tile
                label="IV"
                value={ivNow == null ? "–" : `${(ivNow * 100).toFixed(1)}%`}
                hint={`${signed(ivChange == null ? null : ivChange * 100, (v) => v.toFixed(1))} points`}
              />
            </div>
            <div className="strike-charts">
              <div>
                <div className="strike-chart-head">
                  <span>Price</span>
                  <span className="muted">Last traded price</span>
                </div>
                <LineChart
                  series={price}
                  height={240}
                  tickX={timeTicks}
                  label={`Price of ${name} through the day`}
                  formatX={clock}
                  formatY={(y) => num(y, y < 100 ? 1 : 0)}
                  formatTipX={clock}
                  formatTipY={(y) => num(y, 2)}
                />
              </div>
              <div className="grid-2">
                <div>
                  <div className="strike-chart-head">
                    <span>Open interest</span>
                  </div>
                  <LineChart
                    series={oi}
                    height={180}
                    tickX={timeTicks}
                    label={`Open interest of ${name} through the day`}
                    formatX={clock}
                    formatY={compact}
                    formatTipX={clock}
                    formatTipY={(y) => num(y)}
                  />
                </div>
                <div>
                  <div className="strike-chart-head">
                    <span>Implied volatility</span>
                  </div>
                  <LineChart
                    series={iv}
                    height={180}
                    minSpan={1}
                    tickX={timeTicks}
                    label={`Implied volatility of ${name} through the day`}
                    formatX={clock}
                    formatY={(y) => `${y.toFixed(0)}%`}
                    formatTipX={clock}
                    formatTipY={(y) => `${y.toFixed(1)}%`}
                  />
                </div>
              </div>
            </div>
            <p className="panel-note">
              Click any call or put in the option chain to chart it. Contracts in the live feed show
              one point a minute; the rest, mostly far from the money, show the 5-minute snapshots
              that cover every strike.
            </p>
          </>
        )}
      </Panel>
    </div>
  );
});

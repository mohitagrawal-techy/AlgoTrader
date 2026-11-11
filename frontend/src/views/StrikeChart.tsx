import { useEffect, useMemo, useState } from "react";
import { api, type MarketPayload, type StrikeHistory, type StrikePoint } from "../api";
import { LineChart, timeTicks } from "../charts";
import { compact, num, shortDate, timeIst } from "../format";
import { atmStrike, strikePosition } from "../moneyness";
import { Empty, Panel, Select, Status, Tile } from "../ui";
import { UnderlyingSection } from "./UnderlyingSection";

const PRICE = "var(--series-1)";
const OI = "var(--series-3)";
const IV = "var(--series-5)";
const GREEK_COLOR = "var(--series-2)";

type Greek = "delta" | "gamma" | "theta" | "vega";
const GREEK_CHARTS: { key: Greek; title: string; note: string; digits: number }[] = [
  { key: "delta", title: "Delta", note: "Price change per 1-point move in spot", digits: 3 },
  { key: "gamma", title: "Gamma", note: "Delta change per 1-point move in spot", digits: 4 },
  { key: "theta", title: "Theta", note: "Rupees lost per unit per day", digits: 2 },
  { key: "vega", title: "Vega", note: "Rupees per unit per volatility point", digits: 2 },
];
// The latest day keeps updating while the page is open.
const REFRESH_MS = 60_000;
const CHART_PATH = "market/chart";

export type OptionSide = "CE" | "PE";

/** One contract on one day. An empty day means the newest saved day. */
export interface StrikeRoute {
  symbol: string;
  expiry: string;
  strike: number;
  type: OptionSide;
  day: string;
}

/** The page address, such as #market/chart/NIFTY/2026-11-17/24000/CE. */
export function strikeHash(route: Omit<StrikeRoute, "day"> & { day?: string }): string {
  const parts = [route.symbol, route.expiry, String(route.strike), route.type];
  if (route.day) parts.push(route.day);
  return `#${CHART_PATH}/${parts.map(encodeURIComponent).join("/")}`;
}

/** The chart route in the address bar, or null when another page is open. */
export function readStrikeRoute(): StrikeRoute | null {
  const parts = window.location.hash.slice(1).split("/").map(decodeURIComponent);
  if (`${parts[0]}/${parts[1]}` !== CHART_PATH) return null;
  const [symbol, expiry, strike, type, day = ""] = parts.slice(2);
  if (!symbol || !expiry || !Number.isFinite(Number(strike))) return null;
  if (type !== "CE" && type !== "PE") return null;
  return { symbol, expiry, strike: Number(strike), type, day };
}

/** Opens the chart page, or a new browser tab when Ctrl or ⌘ is held. */
export function openStrike(route: Omit<StrikeRoute, "day">, newTab = false): void {
  const hash = strikeHash(route);
  if (newTab) window.open(`${window.location.pathname}${hash}`, "_blank", "noopener");
  else window.location.hash = hash;
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
  return from == null || to == null ? null : to - from;
}

function signed(value: number | null, format: (v: number) => string) {
  if (value == null) return "–";
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${format(Math.abs(value))}`;
}

function useStrikeHistory(route: StrikeRoute) {
  const { symbol, expiry, strike, type, day } = route;
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
        .marketStrike(symbol, expiry, strike, type, day || undefined)
        .then((data) => active && setState({ data, error: null, loading: false }))
        .catch((err: Error) => active && setState((s) => ({ ...s, error: err.message, loading: false })));
    };
    load(false);
    // Only the newest day changes, so an older day is loaded once.
    const timer = day
      ? undefined
      : window.setInterval(() => {
          if (document.visibilityState === "visible") load(true);
        }, REFRESH_MS);
    return () => {
      active = false;
      if (timer) window.clearInterval(timer);
    };
  }, [symbol, expiry, strike, type, day]);
  return state;
}

function nearest(values: number[], target: number): number {
  return values.reduce((best, v) => (Math.abs(v - target) < Math.abs(best - target) ? v : best), values[0]);
}

export function StrikePage({ route, market }: { route: StrikeRoute; market: MarketPayload | null }) {
  const { data, error, loading } = useStrikeHistory(route);
  const shown =
    data && data.strike === route.strike && data.option_type === route.type && data.expiry === route.expiry
      ? data
      : null;
  const points = useMemo(() => shown?.points ?? [], [shown]);
  const symbolMarket = market?.symbols[route.symbol];
  const symbols = Object.keys(market?.symbols ?? {});
  const expiries = symbolMarket?.expiries ?? [];
  const spot = symbolMarket?.spot ?? null;

  // Menu changes replace the address, so Back returns to the option chain.
  const go = (next: Partial<StrikeRoute>) =>
    window.location.replace(strikeHash({ ...route, ...next }));
  const chooseSymbol = (symbol: string) => {
    const target = market?.symbols[symbol];
    const expiry = target?.expiries[0];
    if (!target || !expiry) return;
    const strikes = expiry.rows.map((row) => row.strike);
    go({ symbol, expiry: expiry.expiry, strike: nearest(strikes, target.spot), day: "" });
  };
  const chooseExpiry = (expiry: string) => {
    const rows = expiries.find((e) => e.expiry === expiry)?.rows ?? [];
    const strikes = rows.map((row) => row.strike);
    go({ expiry, strike: strikes.includes(route.strike) || !strikes.length ? route.strike : nearest(strikes, spot ?? route.strike) });
  };

  const price = useMemo(() => [{ name: "LTP", color: PRICE, points: line(points, (p) => p.ltp) }], [points]);
  const oi = useMemo(() => [{ name: "OI", color: OI, points: line(points, (p) => p.oi) }], [points]);
  const iv = useMemo(
    () => [{ name: "IV", color: IV, points: line(points, (p) => (p.iv == null ? null : p.iv * 100)) }],
    [points],
  );
  const volume = useMemo(() => {
    // Volume is cumulative for the day, so each interval's traded quantity is the difference.
    let previous: number | null = null;
    return [
      {
        name: "Volume",
        color: PRICE,
        points: points.map((p) => {
          const step = p.volume != null && previous != null ? Math.max(p.volume - previous, 0) : null;
          if (p.volume != null) previous = p.volume;
          return { x: p.t, y: step };
        }),
      },
    ];
  }, [points]);

  const greeks = useMemo(
    () =>
      GREEK_CHARTS.map((greek) => ({
        ...greek,
        series: [{ name: greek.title, color: GREEK_COLOR, points: line(points, (p) => p[greek.key]) }],
        now: last(points, (p) => p[greek.key]),
        change: change(first(points, (p) => p[greek.key]), last(points, (p) => p[greek.key])),
      })),
    [points],
  );

  const open = first(points, (p) => p.ltp);
  const close = last(points, (p) => p.ltp);
  const prices = points.map((p) => p.ltp).filter((v): v is number => v != null);
  const oiChange = change(first(points, (p) => p.oi), last(points, (p) => p.oi));
  const ivNow = last(points, (p) => p.iv);
  const ivChange = change(first(points, (p) => p.iv), ivNow);
  const days = data?.days ?? [];
  const saved = data?.expiry === route.expiry && data.strikes.length ? data.strikes : (expiries.find((e) => e.expiry === route.expiry)?.rows.map((r) => r.strike) ?? []);
  const strikes = saved.includes(route.strike) ? saved : [...saved, route.strike].sort((a, b) => a - b);
  const side = route.type === "CE" ? "call" : "put";
  const name = `${route.symbol} ${num(route.strike)} ${route.type}`;
  // ATM is the listed strike nearest spot; others count strikes away from it, as in the chain.
  const atm = spot == null || !strikes.length ? null : atmStrike(strikes, spot);
  const position = (strike: number) =>
    atm == null ? null : strikePosition(strikes, strike, atm, route.type === "CE");
  const here = position(route.strike);

  return (
    <>
      <div className="strike-page-head">
        <a className="button ghost" href="#market">
          ← Option chain
        </a>
        <div>
          <h2 className="strike-page-title">
            {name}{" "}
            {here && <span className={`moneyness moneyness-lg ${here.state.toLowerCase()}`}>{here.label}</span>}{" "}
            <span className="muted">· {shortDate(route.expiry)} expiry</span>
          </h2>
          <span className="muted">
            {route.type === "CE" ? "Call" : "Put"}
            {here ? `, ${here.label}` : ""}
            {spot != null ? ` (spot ${num(spot, 2)})` : ""}
          </span>
        </div>
      </div>

      <div className="filters">
        <Select
          label="Underlying"
          value={route.symbol}
          onChange={chooseSymbol}
          options={(symbols.length ? symbols : [route.symbol]).map((s) => ({ value: s, label: s }))}
        />
        <Select
          label="Expiry"
          value={route.expiry}
          onChange={chooseExpiry}
          options={
            expiries.some((e) => e.expiry === route.expiry)
              ? expiries.map((e) => ({ value: e.expiry, label: `${shortDate(e.expiry)} (${e.dte}d)` }))
              : [{ value: route.expiry, label: shortDate(route.expiry) }]
          }
        />
        <Select
          label="Strike"
          value={String(route.strike)}
          onChange={(value) => go({ strike: Number(value) })}
          options={strikes.map((s) => {
            const at = position(s);
            return { value: String(s), label: at ? `${num(s)} · ${at.label}` : num(s) };
          })}
        />
        <Select
          label="Side"
          value={route.type}
          onChange={(type) => go({ type })}
          options={[
            { value: "CE", label: "Call" },
            { value: "PE", label: "Put" },
          ]}
        />
        <Select
          label="Day"
          value={route.day}
          onChange={(day) => go({ day })}
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
      </div>

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

          <Panel title={`${name} price`} aside={<span className="muted">Last traded price</span>}>
            <LineChart
              series={price}
              height={320}
              tickX={timeTicks}
              label={`Price of the ${name} ${side} through the day`}
              formatX={clock}
              formatY={(y) => num(y, y < 100 ? 1 : 0)}
              formatTipX={clock}
              formatTipY={(y) => num(y, 2)}
            />
          </Panel>

          <h3 className="section-title">{route.symbol} and indicators</h3>
          <UnderlyingSection
            symbol={route.symbol}
            expiry={route.expiry}
            day={route.day}
            strike={route.strike}
            type={route.type}
            premium={close}
            buildup={shown.buildup ?? []}
          />

          <h3 className="section-title">{name} detail</h3>
          <div className="grid-2">
            <Panel title="Open interest">
              <LineChart
                series={oi}
                height={240}
                tickX={timeTicks}
                label={`Open interest of the ${name} ${side} through the day`}
                formatX={clock}
                formatY={compact}
                formatTipX={clock}
                formatTipY={(y) => num(y)}
              />
            </Panel>
            <Panel title="Implied volatility">
              <LineChart
                series={iv}
                height={240}
                minSpan={1}
                tickX={timeTicks}
                label={`Implied volatility of the ${name} ${side} through the day`}
                formatX={clock}
                formatY={(y) => `${y.toFixed(0)}%`}
                formatTipX={clock}
                formatTipY={(y) => `${y.toFixed(1)}%`}
              />
            </Panel>
          </div>
          <div className="grid-2">
            {greeks.map((greek) => (
              <Panel
                key={greek.key}
                title={`${greek.title}${greek.now == null ? "" : ` ${num(greek.now, greek.digits)}`}`}
                aside={
                  <span className="muted">
                    {greek.change == null
                      ? greek.note
                      : `${signed(greek.change, (v) => num(v, greek.digits))} in the day`}
                  </span>
                }
              >
                <LineChart
                  series={greek.series}
                  height={200}
                  tickX={timeTicks}
                  label={`${greek.title} of the ${name} ${side} through the day. ${greek.note}.`}
                  formatX={clock}
                  formatY={(y) => num(y, greek.digits > 3 ? greek.digits : 2)}
                  formatTipX={clock}
                  formatTipY={(y) => num(y, greek.digits)}
                />
                <p className="panel-note">{greek.note}.</p>
              </Panel>
            ))}
          </div>
          <Panel
            title="Volume"
            aside={<span className="muted">{shown.source === "ticks" ? "Per minute" : "Per 5 minutes"}</span>}
          >
            <LineChart
              series={volume}
              height={180}
              includeZero
              tickX={timeTicks}
              label={`Traded volume of the ${name} ${side} through the day`}
              formatX={clock}
              formatY={compact}
              formatTipX={clock}
              formatTipY={(y) => num(y)}
            />
          </Panel>
          <p className="panel-note">
            Contracts in the live feed show one point a minute; the rest, mostly far from the money,
            show the 5-minute snapshots that cover every strike. The chart starts at the 09:15 open.
            Greeks are the broker's, per unit; they are blank where it had no implied volatility.
          </p>
        </>
      )}
    </>
  );
}

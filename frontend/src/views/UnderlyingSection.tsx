import { useEffect, useMemo, useState } from "react";
import { api, type Buildup, type BuildupStep, type UnderlyingView } from "../api";
import { LineChart, timeTicks } from "../charts";
import { compact, num, shortDate, timeIst } from "../format";
import { Empty, Panel, Status, Tile, type Tone } from "../ui";

const SPOT = "var(--series-1)";
const VWAP = "var(--series-4)";
const EMA_FAST = "var(--series-3)";
const EMA_SLOW = "var(--series-5)";
const CONTRACT = "var(--series-2)";
const LEVEL = "var(--ink-3)";
const RANGE = "var(--ink-2)";
const REFRESH_MS = 60_000;
const OVERLAY_KEY = "algoverse-underlying-overlays";
const BUILDUP_ROWS = 8;

const OVERLAYS = {
  vwap: "VWAP",
  ema: "EMA 9 / 21",
  contract: "Strike & breakeven",
  previous: "Previous day H/L/C",
  pivots: "Pivots",
  opening: "Opening range",
} as const;
type Overlay = keyof typeof OVERLAYS;
const DEFAULT_OVERLAYS: Overlay[] = ["vwap", "ema", "contract", "previous"];

export const BUILDUP_LABELS: Record<Buildup, { label: string; tone: Tone }> = {
  long_buildup: { label: "Long build-up", tone: "good" },
  short_covering: { label: "Short covering", tone: "good" },
  short_buildup: { label: "Short build-up", tone: "critical" },
  long_unwinding: { label: "Long unwinding", tone: "warning" },
  neutral: { label: "No clear change", tone: "neutral" },
};

function clock(ms: number): string {
  return timeIst(new Date(ms).toISOString());
}

function signed(value: number | null | undefined, digits = 2): string {
  if (value == null) return "–";
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${num(Math.abs(value), digits)}`;
}

function signedCompact(value: number): string {
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${compact(Math.abs(value))}`;
}

function readOverlays(): Overlay[] {
  try {
    const saved = JSON.parse(localStorage.getItem(OVERLAY_KEY) ?? "null");
    if (Array.isArray(saved)) return saved.filter((key): key is Overlay => key in OVERLAYS);
  } catch {
    // Fall back to the defaults when storage is blocked or holds something else.
  }
  return DEFAULT_OVERLAYS;
}

function useUnderlying(symbol: string, expiry: string, day: string) {
  const [state, setState] = useState<{ data: UnderlyingView | null; error: string | null }>({
    data: null,
    error: null,
  });
  useEffect(() => {
    let active = true;
    const load = () =>
      api
        .marketUnderlying(symbol, expiry, day || undefined)
        .then((data) => active && setState({ data, error: null }))
        .catch((err: Error) => active && setState((s) => ({ ...s, error: err.message })));
    load();
    const timer = day
      ? undefined
      : window.setInterval(() => document.visibilityState === "visible" && load(), REFRESH_MS);
    return () => {
      active = false;
      if (timer) window.clearInterval(timer);
    };
  }, [symbol, expiry, day]);
  return state;
}

/** A flat line at `value`, on the same time points as the spot series. */
function flat(name: string, color: string, value: number | undefined, times: number[]) {
  return value == null ? [] : [{ name, color, points: times.map((x) => ({ x, y: value })) }];
}

export function UnderlyingSection({
  symbol,
  expiry,
  day,
  strike,
  type,
  premium,
  buildup,
}: {
  symbol: string;
  expiry: string;
  day: string;
  strike: number;
  type: "CE" | "PE";
  /** The option's latest price, for the breakeven at expiry. */
  premium: number | null;
  buildup: BuildupStep[];
}) {
  const { data, error } = useUnderlying(symbol, expiry, day);
  const [overlays, setOverlays] = useState<Overlay[]>(readOverlays);
  const toggle = (key: Overlay) => {
    const next = overlays.includes(key) ? overlays.filter((k) => k !== key) : [...overlays, key];
    setOverlays(next);
    try {
      localStorage.setItem(OVERLAY_KEY, JSON.stringify(next));
    } catch {
      // The choice still applies for this visit.
    }
  };

  const view = data && data.symbol === symbol ? data : null;
  const points = useMemo(() => view?.points ?? [], [view]);
  const levels = view?.levels ?? {};
  const breakeven = premium == null ? null : type === "CE" ? strike + premium : strike - premium;

  const spotSeries = useMemo(() => {
    const times = points.map((p) => p.t);
    const on = (key: Overlay) => overlays.includes(key);
    return [
      { name: symbol, color: SPOT, points: points.map((p) => ({ x: p.t, y: p.spot })) },
      ...(on("vwap") && points.some((p) => p.vwap != null)
        ? [{ name: "VWAP", color: VWAP, points: points.map((p) => ({ x: p.t, y: p.vwap })) }]
        : []),
      ...(on("ema")
        ? [
            { name: "EMA 9", color: EMA_FAST, points: points.map((p) => ({ x: p.t, y: p.ema_fast })) },
            { name: "EMA 21", color: EMA_SLOW, points: points.map((p) => ({ x: p.t, y: p.ema_slow })) },
          ]
        : []),
      ...(on("contract")
        ? [
            ...flat(`Strike ${num(strike)}`, CONTRACT, strike, times),
            ...flat("Breakeven", CONTRACT, breakeven ?? undefined, times),
          ]
        : []),
      ...(on("previous")
        ? [
            ...flat("Prev high", LEVEL, levels.prev_high, times),
            ...flat("Prev low", LEVEL, levels.prev_low, times),
            ...flat("Prev close", LEVEL, levels.prev_close, times),
          ]
        : []),
      ...(on("pivots")
        ? (["pivot_r2", "pivot_r1", "pivot_p", "pivot_s1", "pivot_s2"] as const).flatMap((key) =>
            flat(key.replace("pivot_", "").toUpperCase(), LEVEL, levels[key], times),
          )
        : []),
      ...(on("opening")
        ? [
            ...flat("Opening high", RANGE, levels.or_high, times),
            ...flat("Opening low", RANGE, levels.or_low, times),
          ]
        : []),
    ];
  }, [points, overlays, levels, strike, breakeven, symbol]);

  const rsiSeries = useMemo(() => {
    const times = points.map((p) => p.t);
    return [
      { name: "RSI 14", color: SPOT, points: points.map((p) => ({ x: p.t, y: p.rsi })) },
      ...flat("70", LEVEL, 70, times),
      ...flat("30", LEVEL, 30, times),
    ];
  }, [points]);
  const pcrSeries = useMemo(
    () => [{ name: "PCR", color: SPOT, points: (view?.pcr ?? []).map((p) => ({ x: p.t, y: p.pcr })) }],
    [view],
  );
  const vixSeries = useMemo(
    () => [{ name: "India VIX", color: SPOT, points: (view?.vix ?? []).map((p) => ({ x: p.t, y: p.vix })) }],
    [view],
  );

  if (error && !view) return <Empty title="Could not load the underlying">{error}</Empty>;
  if (!view) return <Empty title="Loading the underlying…" />;
  if (!points.length) return <Empty title={`No ${symbol} prices saved for this day`} />;

  const latest = points[points.length - 1];
  const spot = latest.spot;
  const vwapGap = spot != null && latest.vwap != null ? spot - latest.vwap : null;
  const trend =
    latest.ema_fast == null || latest.ema_slow == null
      ? null
      : latest.ema_fast > latest.ema_slow
        ? "up"
        : "down";
  const rsi = latest.rsi;
  const pcr = view.pcr;
  const vix = view.vix;
  const lastBuildup = buildup[buildup.length - 1];
  const toBreakeven = spot != null && breakeven != null ? breakeven - spot : null;
  const interval = view.interval_minutes ?? 1;

  return (
    <>
      <div className="tiles tiles-6">
        <Tile
          label="Spot vs VWAP"
          value={vwapGap == null ? "–" : vwapGap >= 0 ? "Above" : "Below"}
          hint={vwapGap == null ? "Needs the live feed" : `${signed(vwapGap)} points`}
        />
        <Tile
          label="EMA 9 / 21"
          value={trend == null ? "–" : trend === "up" ? "9 above 21" : "9 below 21"}
          hint={`${interval}-minute closes`}
        />
        <Tile
          label="RSI 14, 5 min"
          value={num(rsi, 0)}
          hint={rsi == null ? "Too early in the day" : rsi >= 70 ? "Overbought zone" : rsi <= 30 ? "Oversold zone" : "Neutral zone"}
        />
        <Tile
          label="Put-call ratio"
          value={pcr.length ? num(pcr[pcr.length - 1].pcr, 2) : "–"}
          hint={
            pcr.length > 1
              ? `${signed(pcr[pcr.length - 1].pcr - pcr[0].pcr)} since ${clock(pcr[0].t)}, ${shortDate(expiry)}`
              : `Open interest, ${shortDate(expiry)}`
          }
        />
        <Tile
          label="India VIX"
          value={vix.length ? num(vix[vix.length - 1].vix, 2) : "–"}
          hint={vix.length > 1 ? `${signed(vix[vix.length - 1].vix - vix[0].vix)} in the day` : "Needs the live feed"}
        />
        <Tile
          label="Breakeven at expiry"
          value={num(breakeven, 2)}
          hint={
            toBreakeven == null
              ? "Strike ± premium"
              : `${signed(toBreakeven)} pts (${signed((toBreakeven / (spot as number)) * 100)}%) from spot`
          }
        />
      </div>

      <Panel
        title={`${symbol} spot`}
        aside={
          <span className="overlay-toggles">
            {(Object.keys(OVERLAYS) as Overlay[]).map((key) => (
              <label key={key} className={overlays.includes(key) ? "on" : ""}>
                <input type="checkbox" checked={overlays.includes(key)} onChange={() => toggle(key)} />
                {OVERLAYS[key]}
              </label>
            ))}
          </span>
        }
      >
        <LineChart
          series={spotSeries}
          height={340}
          tickX={timeTicks}
          label={`${symbol} spot through the day with the chosen indicators`}
          formatX={clock}
          formatY={(y) => num(y)}
          formatTipX={clock}
          formatTipY={(y) => num(y, 2)}
        />
        <p className="panel-note">
          {view.source === "ticks"
            ? "One point a minute from the live feed. VWAP is the nearest future's, shifted down by its premium to spot."
            : "Spot from the 5-minute chain snapshots; VWAP and India VIX need the live feed."}{" "}
          {view.previous ? `Previous session ${shortDate(view.previous.day)}. ` : ""}
          Pivots are classic floor pivots from that session. The opening range is the first 15
          minutes. Breakeven uses the option&apos;s latest price.
        </p>
      </Panel>

      <div className="grid-2">
        <Panel title="RSI 14" aside={<span className="muted">5-minute closes; 70 and 30 marked</span>}>
          <LineChart
            series={rsiSeries}
            height={180}
            tickX={timeTicks}
            label={`Relative strength index of ${symbol} through the day`}
            formatX={clock}
            formatY={(y) => num(y, 0)}
            formatTipX={clock}
            formatTipY={(y) => num(y, 1)}
          />
        </Panel>
        <Panel title="Put-call ratio" aside={<span className="muted">Open interest, {shortDate(expiry)} expiry</span>}>
          {pcr.length < 2 ? (
            <Empty title="Not enough snapshots yet" />
          ) : (
            <LineChart
              series={pcrSeries}
              height={180}
              minSpan={0.05}
              tickX={timeTicks}
              label={`Put-call open interest ratio of the ${shortDate(expiry)} expiry through the day`}
              formatX={clock}
              formatY={(y) => num(y, 2)}
              formatTipX={clock}
              formatTipY={(y) => num(y, 3)}
            />
          )}
        </Panel>
        <Panel title="India VIX">
          {vix.length < 2 ? (
            <Empty title="Needs the live feed for this day" />
          ) : (
            <LineChart
              series={vixSeries}
              height={180}
              minSpan={0.5}
              tickX={timeTicks}
              label="India VIX through the day"
              formatX={clock}
              formatY={(y) => num(y, 1)}
              formatTipX={clock}
              formatTipY={(y) => num(y, 2)}
            />
          )}
        </Panel>
        <Panel
          title="Open-interest build-up, this option"
          aside={lastBuildup ? <Status tone={BUILDUP_LABELS[lastBuildup.label].tone}>{BUILDUP_LABELS[lastBuildup.label].label}</Status> : undefined}
        >
          {buildup.length === 0 ? (
            <Empty title="Needs at least two 15-minute blocks" />
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>From</th>
                  <th className="right">Price</th>
                  <th className="right">OI</th>
                  <th>Reading</th>
                </tr>
              </thead>
              <tbody>
                {[...buildup]
                  .reverse()
                  .slice(0, BUILDUP_ROWS)
                  .map((step) => (
                    <tr key={step.t}>
                      <td>{clock(step.t)}</td>
                      <td className="right">{signed(step.price_change)}</td>
                      <td className="right">{signedCompact(step.oi_change)}</td>
                      <td>
                        <Status tone={BUILDUP_LABELS[step.label].tone}>{BUILDUP_LABELS[step.label].label}</Status>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          )}
          <p className="panel-note">
            Price and OI change over each 15-minute block. Rising price with rising OI is long
            build-up; falling price with rising OI is short build-up; rising price with falling OI
            is short covering; falling price with falling OI is long unwinding.
          </p>
        </Panel>
      </div>
    </>
  );
}

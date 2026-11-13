import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type IPrimitivePaneRenderer,
  type IPrimitivePaneView,
  type ISeriesApi,
  type ISeriesPrimitive,
  type SeriesAttachedParameter,
  type SeriesType,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { api, type CandleType, type ChartData, type TechCatalog, type TechRule, type TechTimeframe } from "../api";

type Indicator = TechCatalog["indicators"][number];

interface StudyState {
  id: string;
  name: string;
  params: Record<string, number>;
  hidden?: boolean;
}

interface Saved {
  symbol: string;
  timeframe: TechTimeframe;
  candles: CandleType;
  studies: StudyState[];
  signals: boolean;
  /** Sessions shown on each minute timeframe. */
  sessions?: Partial<Record<TechTimeframe, number>>;
}

const STORAGE_KEY = "algoverse.chart.v1";
const TIMEFRAMES: { value: TechTimeframe; label: string }[] = [
  { value: "5m", label: "5m" },
  { value: "15m", label: "15m" },
  { value: "1h", label: "1h" },
  { value: "1d", label: "1D" },
  { value: "1w", label: "1W" },
  { value: "1mo", label: "1M" },
];
const MINUTES = new Set<TechTimeframe>(["5m", "15m", "1h"]);
const RANGES = [
  { sessions: 5, label: "1W" },
  { sessions: 10, label: "2W" },
  { sessions: 21, label: "1M" },
  { sessions: 63, label: "3M" },
  { sessions: 125, label: "6M" },
  { sessions: 250, label: "1Y" },
];
const DEFAULT_RANGE: Partial<Record<TechTimeframe, number>> = { "5m": 10, "15m": 21, "1h": 63 };
// Already drawn by the candles themselves.
const HIDDEN_FROM_PICKER = new Set(["close", "open", "high", "low"]);
const PALETTE = ["--series-1", "--series-5", "--overlay-2", "--series-3", "--series-4", "--series-2"];
const MAX_STUDIES = 12;
// Short names for the legend, as chart platforms show them. The full name is in the tooltip.
const SHORT: Record<string, string> = {
  sma: "SMA",
  ema: "EMA",
  wma: "WMA",
  rsi: "RSI",
  macd: "MACD",
  bbands: "BB",
  atr: "ATR",
  atr_pct: "ATR %",
  supertrend: "Supertrend",
  adx: "ADX",
  stochastic: "Stoch",
  roc: "ROC",
  cci: "CCI",
  williams_r: "%R",
  donchian: "DC",
  prior_high: "Prior high",
  prior_low: "Prior low",
  range_pct: "Range %",
  body_pct: "Body %",
  gap_pct: "Gap %",
  change_pct: "Change %",
  opening_range: "Opening range",
  pivots: "Pivots",
  prev_close: "Prev close",
  prev_high: "Prev high",
  prev_low: "Prev low",
};
const PRICE_HEIGHT = 420;
const PANE_HEIGHT = 120;
// Reference levels drawn in an oscillator's pane.
const LEVELS: Record<string, number[]> = {
  rsi: [30, 70],
  stochastic: [20, 80],
  cci: [-100, 100],
  williams_r: [-80, -20],
  adx: [25],
};
// A sensible threshold when turning an oscillator into a rule.
const MIDPOINT: Record<string, number> = { rsi: 50, stochastic: 50, williams_r: -50, adx: 25 };

function readSaved(symbols: string[]): Saved {
  const fallback: Saved = {
    symbol: symbols[0] ?? "NIFTY",
    timeframe: "1d",
    candles: "normal",
    studies: [
      { id: "s1", name: "ema", params: { period: 20 } },
      { id: "s2", name: "rsi", params: { period: 14 } },
    ],
    signals: true,
  };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return fallback;
    const saved = { ...fallback, ...(JSON.parse(raw) as Partial<Saved>) };
    if (!symbols.includes(saved.symbol)) saved.symbol = fallback.symbol;
    return saved;
  } catch {
    return fallback;
  }
}

function css(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

/** Bumps when the light or dark theme changes, so the chart can recolour. */
function useThemeTick(): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const bump = () => setTick((t) => t + 1);
    const observer = new MutationObserver(bump);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", bump);
    return () => {
      observer.disconnect();
      media.removeEventListener("change", bump);
    };
  }, []);
  return tick;
}

function priceText(value: number | null | undefined): string {
  if (value === null || value === undefined) return "–";
  const digits = Math.abs(value) >= 1000 ? 1 : Math.abs(value) >= 10 ? 2 : 3;
  return value.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function dashed(field: string | null): boolean {
  return field === "mid" || field === "signal" || field === "d" || field === "p";
}

// Channels drawn as two edges with a shaded fill and a solid middle line.
const BANDS: Record<string, { upper: string; lower: string; middle: string }> = {
  bbands: { upper: "upper", lower: "lower", middle: "middle" },
  donchian: { upper: "upper", lower: "lower", middle: "middle" },
};

function withAlpha(colour: string, alpha: number): string {
  const hex = colour.match(/^#([0-9a-f]{6})$/i);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }
  const rgb = colour.match(/^rgba?\(([^)]+)\)$/i);
  if (rgb) {
    const [r, g, b] = rgb[1].split(",").map((part) => part.trim());
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  return colour;
}

/** Shades the area between two lines, such as Bollinger Bands, under the candles. */
class BandFill implements ISeriesPrimitive<Time> {
  private attachedTo: SeriesAttachedParameter<Time> | null = null;
  private readonly view: IPrimitivePaneView;

  constructor(
    private readonly times: Time[],
    private readonly upper: (number | null)[],
    private readonly lower: (number | null)[],
    private readonly colour: string,
  ) {
    const renderer: IPrimitivePaneRenderer = {
      draw: (target) => {
        const attached = this.attachedTo;
        if (!attached) return;
        const scale = attached.chart.timeScale();
        const range = scale.getVisibleLogicalRange();
        if (!range) return;
        const from = Math.max(0, Math.floor(range.from) - 1);
        const to = Math.min(this.times.length - 1, Math.ceil(range.to) + 1);
        target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
          context.fillStyle = this.colour;
          let top: [number, number][] = [];
          let bottom: [number, number][] = [];
          const flush = () => {
            if (top.length > 1) {
              context.beginPath();
              top.forEach(([x, y], i) => (i ? context.lineTo(x, y) : context.moveTo(x, y)));
              for (let i = bottom.length - 1; i >= 0; i--) context.lineTo(bottom[i][0], bottom[i][1]);
              context.closePath();
              context.fill();
            }
            top = [];
            bottom = [];
          };
          for (let i = from; i <= to; i++) {
            const high = this.upper[i];
            const low = this.lower[i];
            const x = scale.timeToCoordinate(this.times[i]);
            const yHigh = high === null ? null : attached.series.priceToCoordinate(high);
            const yLow = low === null ? null : attached.series.priceToCoordinate(low);
            if (x === null || yHigh === null || yLow === null) {
              flush();
              continue;
            }
            top.push([x * hr, yHigh * vr]);
            bottom.push([x * hr, yLow * vr]);
          }
          flush();
        });
      },
    };
    this.view = { zOrder: () => "bottom", renderer: () => renderer };
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.attachedTo = param;
  }

  detached(): void {
    this.attachedTo = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }
}

function newId(): string {
  return `s${Date.now().toString(36)}${Math.floor(Math.random() * 1000)}`;
}

function defaults(indicator: Indicator): Record<string, number> {
  return Object.fromEntries(indicator.params.map((p) => [p.name, p.default]));
}

function ruleFor(study: StudyState, indicator: Indicator, timeframe: TechTimeframe, candles: CandleType): TechRule {
  const field = indicator.fields[0]?.value ?? null;
  const operand = { kind: "indicator" as const, name: study.name, params: study.params, field };
  if (indicator.overlay) {
    return {
      type: "compare",
      timeframe,
      candles,
      left: { kind: "indicator", name: "close" },
      op: "crosses_above",
      right: operand,
    };
  }
  return {
    type: "compare",
    timeframe,
    candles,
    left: operand,
    op: "above",
    right: { kind: "value", value: MIDPOINT[study.name] ?? 0 },
  };
}

function Icon({ path }: { path: string }) {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d={path} />
    </svg>
  );
}

const EYE = "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z";
const EYE_OFF = "M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.2M6.6 6.6C3.7 8.4 2 12 2 12s3.5 7 10 7a9.7 9.7 0 0 0 4.4-1.1M9.9 9.9a3 3 0 0 0 4.2 4.2";
const GEAR = "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z";
const CROSS = "M18 6L6 18M6 6l12 12";
const PLUS_RULE = "M12 5v14M5 12h14";

interface Props {
  catalog: TechCatalog;
  symbols: string[];
  rules: TechRule[];
  anyRules: TechRule[];
  /** Adds a rule built from an indicator on the chart. Absent when rules cannot be added. */
  onAddRule?: (rule: TechRule) => void;
}

export function IndexChart({ catalog, symbols, rules, anyRules, onAddRule }: Props) {
  const [saved, setSaved] = useState<Saved>(() => readSaved(symbols));
  const { symbol, timeframe, candles, studies, signals } = saved;
  const sessions = MINUTES.has(timeframe)
    ? (saved.sessions?.[timeframe] ?? DEFAULT_RANGE[timeframe] ?? 21)
    : undefined;
  const [data, setData] = useState<ChartData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [paneTops, setPaneTops] = useState<number[]>([]);
  const themeTick = useThemeTick();

  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<SeriesType>[]>([]);
  const timesRef = useRef<Map<number, number>>(new Map());
  const pickerRef = useRef<HTMLDivElement>(null);

  const indicators = useMemo(() => new Map(catalog.indicators.map((i) => [i.name, i])), [catalog]);
  const update = (change: Partial<Saved>) => setSaved((current) => ({ ...current, ...change }));

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    } catch {
      // Settings only last for this visit when storage is blocked.
    }
  }, [saved]);

  // ---- Data ----------------------------------------------------------------------------------
  const requestKey = JSON.stringify({
    symbol,
    timeframe,
    candles,
    sessions,
    studies: studies.map(({ id, name, params }) => ({ id, name, params })),
    rules: signals ? rules : [],
    anyRules: signals ? anyRules : [],
  });
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      const body = JSON.parse(requestKey);
      api
        .labChart(
          {
            symbol: body.symbol,
            timeframe: body.timeframe,
            candles: body.candles,
            sessions: body.sessions,
            studies: body.studies,
            rules: body.rules,
            any_rules: body.anyRules,
          },
          controller.signal,
        )
        .then((next) => {
          setData(next);
          setError(null);
          setHover(null);
        })
        .catch((reason) => {
          if (controller.signal.aborted) return;
          setError(reason instanceof Error ? reason.message : "Could not load the chart.");
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 250);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [requestKey]);

  // ---- Chart ---------------------------------------------------------------------------------
  useEffect(() => {
    if (!box.current) return;
    const chart = createChart(box.current, {
      autoSize: true,
      crosshair: { mode: CrosshairMode.Normal },
      // Low enough that Fit can show 26 years of daily candles at once.
      timeScale: { rightOffset: 4, borderVisible: false, minBarSpacing: 0.05 },
      rightPriceScale: { borderVisible: false },
      // The wheel scrolls the page, which is a long form. Drag to pan, and zoom with the
      // buttons, a pinch, or by dragging the time axis.
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true },
    });
    chartRef.current = chart;
    chart.subscribeCrosshairMove((param) => {
      const index = param.time === undefined ? undefined : timesRef.current.get(param.time as number);
      setHover(index ?? null);
    });
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = [];
    };
  }, []);

  const measurePanes = useCallback(() => {
    const chart = chartRef.current;
    if (!chart) return;
    let top = 0;
    const tops: number[] = [];
    for (const pane of chart.panes()) {
      tops.push(top);
      top += pane.getHeight() + 1;
    }
    setPaneTops(tops);
  }, []);

  useEffect(() => {
    if (!box.current) return;
    const observer = new ResizeObserver(() => window.requestAnimationFrame(measurePanes));
    observer.observe(box.current);
    return () => observer.disconnect();
  }, [measurePanes]);

  // Which pane each separate-pane study draws in, counted from 1.
  const panes = useMemo(() => {
    const found = new Map<string, number>();
    if (!data || data.empty) return found;
    for (const study of data.studies) {
      const state = studies.find((s) => s.id === study.id);
      if (state?.hidden || study.error) continue;
      if (study.lines.some((line) => !line.overlay)) found.set(study.id, found.size + 1);
    }
    return found;
  }, [data, studies]);

  const colours = useMemo(() => {
    const found = new Map<string, string>();
    studies.forEach((study, index) => found.set(study.id, PALETTE[index % PALETTE.length]));
    return found;
  }, [studies]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const ink = css("--ink-2");
    const tick = css("--tick");
    const grid = css("--grid");
    chart.applyOptions({
      layout: {
        background: { color: "transparent" },
        textColor: tick,
        fontFamily: getComputedStyle(document.body).fontFamily,
        fontSize: 11,
        panes: { separatorColor: css("--border"), separatorHoverColor: css("--accent-wash") },
      },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      crosshair: {
        vertLine: { color: tick, labelBackgroundColor: ink },
        horzLine: { color: tick, labelBackgroundColor: ink },
      },
      timeScale: { timeVisible: MINUTES.has(timeframe), secondsVisible: false },
    });

    for (const series of seriesRef.current) chart.removeSeries(series);
    seriesRef.current = [];
    while (chart.panes().length > 1) chart.removePane(chart.panes().length - 1);
    timesRef.current = new Map();
    if (!data || data.empty) return;

    const times = data.time as UTCTimestamp[];
    times.forEach((t, i) => timesRef.current.set(t, i));
    const up = css("--good");
    const down = css("--critical");
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      borderUpColor: up,
      borderDownColor: down,
      wickUpColor: up,
      wickDownColor: down,
      priceLineVisible: false,
    });
    candleSeries.setData(
      times.flatMap((time, i) => {
        const [o, h, l, c] = [data.open[i], data.high[i], data.low[i], data.close[i]];
        return o === null || h === null || l === null || c === null ? [] : [{ time, open: o, high: h, low: l, close: c }];
      }),
    );
    seriesRef.current.push(candleSeries);

    for (const study of data.studies) {
      const state = studies.find((s) => s.id === study.id);
      if (!state || state.hidden || study.error) continue;
      const colour = css(colours.get(study.id) ?? PALETTE[0]);
      const pane = panes.get(study.id) ?? 0;
      let levelsDrawn = false;
      const band = BANDS[study.name];
      const middleColour = css(colours.get(study.id) === "--series-2" ? "--series-1" : "--series-2");
      const single = study.lines.length === 1;
      study.lines.forEach((line, index) => {
        const paneIndex = line.overlay ? 0 : pane;
        const isMiddle = band && line.field === band.middle;
        const points = times.map((time, i) => {
          const value = line.values[i];
          return value === null ? { time } : { time, value };
        });
        if (line.field === "hist") {
          const bars = chart.addSeries(
            HistogramSeries,
            { priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "price", precision: 2, minMove: 0.01 } },
            paneIndex,
          );
          bars.setData(
            points.map((p) =>
              "value" in p ? { ...p, color: (p.value as number) >= 0 ? css("--up-wash") : css("--down-wash") } : p,
            ),
          );
          seriesRef.current.push(bars);
          return;
        }
        const series = chart.addSeries(
          LineSeries,
          {
            color: isMiddle ? middleColour : colour,
            lineWidth: single ? 2 : 1,
            lineStyle: !band && dashed(line.field) ? LineStyle.Dashed : LineStyle.Solid,
            priceLineVisible: false,
            lastValueVisible: band ? Boolean(isMiddle) : index === 0,
            crosshairMarkerVisible: false,
          },
          paneIndex,
        );
        series.setData(points);
        if (band && line.field === band.upper) {
          const lower = study.lines.find((other) => other.field === band.lower);
          if (lower) series.attachPrimitive(new BandFill(times, line.values, lower.values, withAlpha(colour, 0.09)));
        }
        if (paneIndex > 0 && !levelsDrawn) {
          for (const level of LEVELS[study.name] ?? []) {
            series.createPriceLine({
              price: level,
              color: css("--axis"),
              lineWidth: 1,
              lineStyle: LineStyle.Dotted,
              axisLabelVisible: false,
              title: "",
            });
          }
          levelsDrawn = true;
        }
        seriesRef.current.push(series);
      });
    }

    const all = chart.panes();
    if (all.length > 1) {
      all[0].setStretchFactor(PRICE_HEIGHT / PANE_HEIGHT);
      for (const pane of all.slice(1)) pane.setStretchFactor(1);
    }

    if (signals && data.signals.hits.length) {
      createSeriesMarkers(
        candleSeries,
        data.signals.hits.map((i) => ({
          time: times[i] as Time,
          position: "belowBar" as const,
          color: css("--accent"),
          shape: "arrowUp" as const,
        })),
      );
    }
    window.requestAnimationFrame(measurePanes);
  }, [data, studies, panes, colours, signals, timeframe, themeTick, measurePanes]);

  // A new symbol or timeframe starts at the latest candles.
  const lastFit = useRef("");
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !data || data.empty) return;
    const key = `${data.symbol}/${data.timeframe}/${data.time.length}`;
    if (lastFit.current === key) return;
    lastFit.current = key;
    const count = data.time.length;
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, count - 160), to: count + 4 });
  }, [data]);

  const zoom = (factor: number) => {
    const scale = chartRef.current?.timeScale();
    const range = scale?.getVisibleLogicalRange();
    if (!scale || !range) return;
    const width = Math.max(10, (range.to - range.from) * factor);
    scale.setVisibleLogicalRange({ from: range.to - width, to: range.to });
  };

  // ---- Picker --------------------------------------------------------------------------------
  useEffect(() => {
    if (!pickerOpen) return;
    const close = (event: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(event.target as Node)) setPickerOpen(false);
    };
    const escape = (event: KeyboardEvent) => event.key === "Escape" && setPickerOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [pickerOpen]);

  const addStudy = (indicator: Indicator) => {
    if (studies.length >= MAX_STUDIES) return;
    update({ studies: [...studies, { id: newId(), name: indicator.name, params: defaults(indicator) }] });
    setPickerOpen(false);
    setSearch("");
  };
  const setStudy = (id: string, change: Partial<StudyState>) =>
    update({ studies: studies.map((s) => (s.id === id ? { ...s, ...change } : s)) });
  const removeStudy = (id: string) => {
    update({ studies: studies.filter((s) => s.id !== id) });
    if (editing === id) setEditing(null);
  };

  const query = search.trim().toLowerCase();
  const choices = catalog.indicators.filter(
    (i) =>
      !HIDDEN_FROM_PICKER.has(i.name) &&
      (!query || i.label.toLowerCase().includes(query) || i.name.includes(query) || i.description.toLowerCase().includes(query)),
  );
  const groups = [
    { title: "On the price", items: choices.filter((i) => i.overlay) },
    { title: "In their own pane", items: choices.filter((i) => !i.overlay) },
  ].filter((group) => group.items.length);

  // ---- Legend --------------------------------------------------------------------------------
  const count = data && !data.empty ? data.time.length : 0;
  const at = hover ?? (count ? count - 1 : null);
  const ohlc =
    at !== null && data && !data.empty
      ? { o: data.open[at], h: data.high[at], l: data.low[at], c: data.close[at], prev: at > 0 ? data.close[at - 1] : null }
      : null;
  const change = ohlc && ohlc.c !== null && ohlc.prev ? ((ohlc.c - ohlc.prev) / ohlc.prev) * 100 : null;
  const studyData = new Map((data?.studies ?? []).map((s) => [s.id, s]));
  const editingStudy = studies.find((s) => s.id === editing);
  const editingIndicator = editingStudy ? indicators.get(editingStudy.name) : undefined;

  const legendRow = (state: StudyState) => {
    const study = studyData.get(state.id);
    const indicator = indicators.get(state.name);
    const short = indicator?.params.map((p) => state.params[p.name] ?? p.default).join(", ");
    const values = study?.lines.map((line) => (at === null ? null : line.values[at])) ?? [];
    return (
      <div key={state.id} className={`chart-legend-row${state.hidden ? " is-hidden" : ""}`}>
        <span className="chart-swatch" style={{ background: `var(${colours.get(state.id)})` }} aria-hidden />
        <span className="chart-legend-name" title={indicator?.label}>
          {SHORT[state.name] ?? indicator?.label ?? state.name}
          {short ? ` ${short}` : ""}
        </span>
        {study?.error ? (
          <span className="chart-legend-error">{study.error}</span>
        ) : (
          !state.hidden && (
            <span className="chart-legend-values">{values.map((v, i) => <span key={i}>{priceText(v)}</span>)}</span>
          )
        )}
        <span className="chart-legend-tools">
          <button
            type="button"
            className="chart-tool"
            onClick={() => setStudy(state.id, { hidden: !state.hidden })}
            aria-label={state.hidden ? `Show ${indicator?.label}` : `Hide ${indicator?.label}`}
            title={state.hidden ? "Show" : "Hide"}
          >
            <Icon path={state.hidden ? EYE_OFF : EYE} />
          </button>
          {indicator && indicator.params.length > 0 && (
            <button
              type="button"
              className="chart-tool"
              aria-label={`Settings for ${indicator.label}`}
              title="Settings"
              aria-expanded={editing === state.id}
              onClick={() => setEditing(editing === state.id ? null : state.id)}
            >
              <Icon path={GEAR} />
            </button>
          )}
          {onAddRule && indicator && !study?.error && (
            <button
              type="button"
              className="chart-tool"
              aria-label={`Add an entry rule using ${indicator.label}`}
              title="Add as an entry rule"
              onClick={() => onAddRule(ruleFor(state, indicator, timeframe, candles))}
            >
              <Icon path={PLUS_RULE} />
            </button>
          )}
          <button
            type="button"
            className="chart-tool"
            aria-label={`Remove ${indicator?.label}`}
            title="Remove"
            onClick={() => removeStudy(state.id)}
          >
            <Icon path={CROSS} />
          </button>
        </span>
      </div>
    );
  };

  const overlayStudies = studies.filter((s) => !panes.has(s.id));
  const signalInfo = data && !data.empty && signals ? data.signals : null;
  const height = PRICE_HEIGHT + PANE_HEIGHT * panes.size;

  return (
    <div className="index-chart">
      <div className="chart-toolbar" role="toolbar" aria-label="Chart settings">
        <label className="chart-symbol">
          <span className="visually-hidden">Index</span>
          <select value={symbol} onChange={(e) => update({ symbol: e.target.value })}>
            {symbols.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <div className="chart-group" role="group" aria-label="Timeframe">
          {TIMEFRAMES.map((t) => (
            <button
              key={t.value}
              type="button"
              className="chart-chip"
              aria-pressed={timeframe === t.value}
              onClick={() => update({ timeframe: t.value })}
            >
              {t.label}
            </button>
          ))}
        </div>
        {sessions !== undefined && (
          <label className="chart-range">
            <span className="visually-hidden">Range</span>
            <select
              value={sessions}
              title="How many sessions to load"
              onChange={(e) =>
                update({ sessions: { ...saved.sessions, [timeframe]: Number(e.target.value) } })
              }
            >
              {RANGES.map((r) => (
                <option key={r.sessions} value={r.sessions}>
                  {r.label}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="chart-group" role="group" aria-label="Candle type">
          {(catalog.candle_types ?? [{ value: "normal", label: "Normal" }]).map((t) => (
            <button
              key={t.value}
              type="button"
              className="chart-chip"
              aria-pressed={candles === t.value}
              onClick={() => update({ candles: t.value })}
            >
              {t.value === "normal" ? "Candles" : t.label}
            </button>
          ))}
        </div>
        <div className="chart-picker" ref={pickerRef}>
          <button
            type="button"
            className="chart-chip chart-add"
            aria-expanded={pickerOpen}
            aria-haspopup="dialog"
            disabled={studies.length >= MAX_STUDIES}
            onClick={() => setPickerOpen(!pickerOpen)}
          >
            <Icon path="M3 3v18h18M7 14l4-4 3 3 6-6" /> Indicators
          </button>
          {pickerOpen && (
            <div className="chart-picker-panel" role="dialog" aria-label="Add an indicator">
              <input
                autoFocus
                type="search"
                value={search}
                placeholder="Search indicators"
                aria-label="Search indicators"
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && choices[0]) addStudy(choices[0]);
                }}
              />
              <div className="chart-picker-list">
                {groups.map((group) => (
                  <div key={group.title}>
                    <div className="chart-picker-group">{group.title}</div>
                    {group.items.map((indicator) => {
                      const blocked = indicator.intraday_only && !MINUTES.has(timeframe);
                      return (
                        <button
                          key={indicator.name}
                          type="button"
                          className="chart-picker-item"
                          onClick={() => addStudy(indicator)}
                          title={blocked ? "Needs 5-minute, 15-minute or hourly candles" : indicator.description}
                        >
                          <span>{indicator.label}</span>
                          {(indicator.description || blocked) && (
                            <small>{blocked ? "Minute candles only" : indicator.description}</small>
                          )}
                        </button>
                      );
                    })}
                  </div>
                ))}
                {!groups.length && <p className="chart-picker-empty">No indicator matches "{search}".</p>}
              </div>
            </div>
          )}
        </div>
        <label className="chart-toggle">
          <input type="checkbox" checked={signals} onChange={(e) => update({ signals: e.target.checked })} />
          Entry signals
        </label>
        <div className="chart-group" role="group" aria-label="Zoom">
          <button type="button" className="chart-chip" onClick={() => zoom(0.6)} aria-label="Zoom in" title="Zoom in">
            +
          </button>
          <button type="button" className="chart-chip" onClick={() => zoom(1.6)} aria-label="Zoom out" title="Zoom out">
            −
          </button>
          <button
            type="button"
            className="chart-chip"
            onClick={() => chartRef.current?.timeScale().fitContent()}
            title="Show all loaded candles"
          >
            Fit
          </button>
        </div>
      </div>

      {editingStudy && editingIndicator && (
        <div className="chart-settings" role="group" aria-label={`${editingIndicator.label} settings`}>
          <strong>{editingIndicator.label}</strong>
          {editingIndicator.params.map((param) => (
            <label key={param.name}>
              {param.label}
              <input
                type="number"
                min={param.min}
                max={param.max}
                step={param.whole ? 1 : 0.1}
                value={editingStudy.params[param.name] ?? param.default}
                onChange={(e) => {
                  const raw = Number(e.target.value);
                  if (!Number.isFinite(raw)) return;
                  const value = Math.min(param.max, Math.max(param.min, param.whole ? Math.round(raw) : raw));
                  setStudy(editingStudy.id, { params: { ...editingStudy.params, [param.name]: value } });
                }}
              />
            </label>
          ))}
          <button type="button" className="chart-chip" onClick={() => setEditing(null)}>
            Done
          </button>
        </div>
      )}

      <div className="chart-stage" style={{ height }}>
        <div ref={box} className="chart-canvas" />
        <div className="chart-legend" style={{ top: (paneTops[0] ?? 0) + 6 }}>
          <div className="chart-legend-head">
            <strong>{symbol}</strong>
            <span className="muted">
              {TIMEFRAMES.find((t) => t.value === timeframe)?.label}
              {candles === "heikin_ashi" ? " · Heikin-Ashi" : ""}
            </span>
            {ohlc && (
              <span className="chart-ohlc">
                O <b>{priceText(ohlc.o)}</b> H <b>{priceText(ohlc.h)}</b> L <b>{priceText(ohlc.l)}</b> C{" "}
                <b>{priceText(ohlc.c)}</b>
                {change !== null && (
                  <b className={change >= 0 ? "pnl-up" : "pnl-down"}>
                    {" "}
                    {change >= 0 ? "+" : ""}
                    {change.toFixed(2)}%
                  </b>
                )}
              </span>
            )}
            {loading && <span className="muted">Updating…</span>}
          </div>
          {overlayStudies.map(legendRow)}
        </div>
        {[...panes.entries()].map(([id, pane]) => {
          const state = studies.find((s) => s.id === id);
          return state ? (
            <div key={id} className="chart-legend" style={{ top: (paneTops[pane] ?? 0) + 4 }}>
              {legendRow(state)}
            </div>
          ) : null;
        })}
        {(error || data?.empty) && <div className="chart-empty">{error ?? data?.empty}</div>}
      </div>

      <div className="chart-foot">
        {data?.source && (
          <span className="muted">
            {data.source}
            {data.first ? `, from ${data.first}` : ""}. Indicators use the same code as backtests.
          </span>
        )}
        {signalInfo && (
          <div className="chart-signals">
            {signalInfo.rules.length ? (
              <span>
                <span className="chart-signal-mark" aria-hidden>
                  ▲
                </span>{" "}
                Entry rules held on <strong>{signalInfo.hits.length}</strong> of {count} candles shown.
                {signalInfo.rules.map((rule) => (
                  <span key={`${rule.group}-${rule.text}`} className="chart-signal-rule">
                    {rule.group === "any" ? "Any of: " : ""}
                    {rule.text}: {rule.count}
                  </span>
                ))}
              </span>
            ) : (
              <span className="muted">
                {rules.length + anyRules.length
                  ? "No entry rule uses this timeframe."
                  : "Add entry rules to see where they would have held."}
              </span>
            )}
            {signalInfo.other_timeframes.length > 0 && (
              <span className="muted">
                Not shown, other timeframes: {signalInfo.other_timeframes.join("; ")}.
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

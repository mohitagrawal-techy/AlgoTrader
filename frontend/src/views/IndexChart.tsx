import {
  ChartLine,
  Eye,
  EyeOff,
  Maximize2,
  Minimize2,
  Plus,
  Settings,
  X,
} from "lucide-react";
import { Alert, ChartChip } from "../components/ui";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BaselineSeries,
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  LineType,
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
import {
  api,
  type CandleType,
  type ChartData,
  type IndicatorPick,
  type LabSpec,
  type ReplayData,
  type TechCatalog,
  type TechRule,
  type TechTimeframe,
} from "../api";
import { ReplayControls, ReplayPanel } from "./ReplayPanel";
import { indexAt, timeline, wallText } from "./replay";
import { IndicatorTip } from "./IndicatorTip";
import { byType, pickText } from "../builder/logic/picks";

type Indicator = TechCatalog["indicators"][number];

interface StudyState {
  id: string;
  name: string;
  params: Record<string, number>;
  hidden?: boolean;
  /** Drawn because the strategy's rules read it, not added by hand. */
  strategy?: boolean;
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
  { sessions: 500, label: "2Y" },
];
const DEFAULT_RANGE: Partial<Record<TechTimeframe, number>> = {
  "5m": 10,
  "15m": 21,
  "1h": 63,
};
// Already drawn by the candles themselves.
// The candles themselves: on a chart they would only redraw what is already there.
const HIDDEN_FROM_PICKER = new Set([
  "close",
  "open",
  "high",
  "low",
  "candle",
  "prev_candle",
]);
const PALETTE = [
  "--series-1",
  "--series-5",
  "--overlay-2",
  "--series-3",
  "--series-4",
  "--series-2",
];
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
  opening_range_body: "Opening range, open–close",
  pivots: "Pivots",
  hma: "HMA",
  smma: "SMMA",
  keltner: "KC",
  ichimoku: "Ichimoku",
  psar: "SAR",
  aroon: "Aroon",
  awesome: "AO",
  chandelier: "Chandelier",
  coppock: "Coppock",
  fisher: "Fisher",
  halftrend: "HalfTrend",
  stoch_rsi: "Stoch RSI",
  tii: "TII",
  alligator: "Alligator",
  qqe: "QQE",
  rsi_ma: "RSI + MA",
  atr_ma: "ATR + MA",
  cpr: "CPR",
  today: "Today",
  prev_day: "Prev day",
  candle_at: "Candle at",
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
  stoch_rsi: [20, 80],
  rsi_ma: [30, 70],
  tii: [20, 80],
  qqe: [50],
  aroon: [30, 70],
  awesome: [0],
  coppock: [0],
  fisher: [0],
};
// A sensible threshold when turning an oscillator into a rule.
const MIDPOINT: Record<string, number> = {
  rsi: 50,
  stochastic: 50,
  williams_r: -50,
  adx: 25,
  stoch_rsi: 50,
  rsi_ma: 50,
  tii: 50,
  qqe: 50,
  awesome: 0,
  coppock: 0,
  fisher: 0,
};
// Levels that hold for a whole session and then jump: drawn as steps, not slopes.
const STEPPED = new Set(["cpr", "today", "prev_day", "candle_at", "pivots"]);
// Drawn as dots under or over the candles, as charting platforms show them.
const DOTTED = new Set(["psar"]);

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
  return (
    getComputedStyle(document.documentElement).getPropertyValue(name).trim() ||
    "#888"
  );
}

/** Bumps when the light or dark theme changes, so the chart can recolour. */
function useThemeTick(): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const bump = () => setTick((t) => t + 1);
    const observer = new MutationObserver(bump);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
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
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function dashed(field: string | null): boolean {
  return (
    field === "mid" || field === "signal" || field === "d" || field === "p"
  );
}

// Channels drawn as two edges with a shaded fill and a solid middle line.
const BANDS: Record<string, { upper: string; lower: string; middle: string }> =
  {
    bbands: { upper: "upper", lower: "lower", middle: "middle" },
    donchian: { upper: "upper", lower: "lower", middle: "middle" },
    keltner: { upper: "upper", lower: "lower", middle: "middle" },
    // The cloud between the leading spans; the base line is the "middle".
    ichimoku: { upper: "span_a", lower: "span_b", middle: "base" },
    // One shaded box per session: empty until the range is known, so each day stands alone.
    opening_range: { upper: "high", lower: "low", middle: "mid" },
    opening_range_body: { upper: "high", lower: "low", middle: "mid" },
  };
// Ranges that reset every session, drawn as one box per day rather than as lines.
const SESSION_BOXES = new Set(["opening_range", "opening_range_body"]);

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
    /** Also draw the edges, and a dashed middle, breaking wherever a value is missing. */
    private readonly edges?: { stroke: string; middle?: (number | null)[] },
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
        target.useBitmapCoordinateSpace(
          ({ context, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
            context.fillStyle = this.colour;
            let top: [number, number][] = [];
            let bottom: [number, number][] = [];
            let middle: [number, number][] = [];
            const line = (points: [number, number][], dash: number[]) => {
              if (points.length < 2) return;
              context.setLineDash(dash);
              context.beginPath();
              points.forEach(([x, y], i) =>
                i ? context.lineTo(x, y) : context.moveTo(x, y),
              );
              context.stroke();
            };
            const flush = () => {
              if (top.length > 1) {
                context.beginPath();
                top.forEach(([x, y], i) =>
                  i ? context.lineTo(x, y) : context.moveTo(x, y),
                );
                for (let i = bottom.length - 1; i >= 0; i--)
                  context.lineTo(bottom[i][0], bottom[i][1]);
                context.closePath();
                context.fill();
                if (this.edges) {
                  context.strokeStyle = this.edges.stroke;
                  context.lineWidth = Math.max(1, Math.round(1.5 * hr));
                  line(top, []);
                  line(bottom, []);
                  context.lineWidth = Math.max(1, Math.round(hr));
                  line(middle, [4 * hr, 3 * hr]);
                  context.setLineDash([]);
                }
              }
              top = [];
              bottom = [];
              middle = [];
            };
            for (let i = from; i <= to; i++) {
              const high = this.upper[i];
              const low = this.lower[i];
              const x = scale.timeToCoordinate(this.times[i]);
              const yHigh =
                high === null ? null : attached.series.priceToCoordinate(high);
              const yLow =
                low === null ? null : attached.series.priceToCoordinate(low);
              if (x === null || yHigh === null || yLow === null) {
                flush();
                continue;
              }
              top.push([x * hr, yHigh * vr]);
              bottom.push([x * hr, yLow * vr]);
              const mid = this.edges?.middle?.[i];
              const yMid =
                mid === null || mid === undefined
                  ? null
                  : attached.series.priceToCoordinate(mid);
              if (yMid !== null) middle.push([x * hr, yMid * vr]);
            }
            flush();
          },
        );
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

// The candles themselves already show these.
// Prices a rule reads, drawn as the level they are compared with rather than as a line.
const PRICE_OPERANDS = new Set([
  "close",
  "open",
  "high",
  "low",
  "candle",
  "prev_candle",
]);

interface StrategyLines {
  studies: StudyState[];
  /** Fixed levels a rule compares an indicator with, by study key and field. */
  levels: Map<string, { field: string | null; value: number }[]>;
  /** Fixed levels compared with the price itself. */
  priceLevels: number[];
  /** Rules on another timeframe or candle type, which this chart cannot draw. */
  elsewhere: number;
  /** The strategy's indicators that read other candles, so are not drawn on these. */
  otherPicks: IndicatorPick[];
}

function studyKey(name: string, params: Record<string, number>): string {
  return `${name}:${Object.keys(params)
    .sort()
    .map((key) => `${key}=${params[key]}`)
    .join(",")}`;
}

/** What the strategy's rules read on this chart's candles: their indicators, at their
 * parameters, and the fixed levels they compare with. */
function strategyLines(
  draft: LabSpec | undefined,
  indicators: Map<string, Indicator>,
  timeframe: TechTimeframe,
  candles: CandleType,
  user: StudyState[],
): StrategyLines {
  const found: StrategyLines = {
    studies: [],
    levels: new Map(),
    priceLevels: [],
    elsewhere: 0,
    otherPicks: [],
  };
  if (!draft) return found;
  const all = [
    ...(draft.entry.rules ?? []),
    ...(draft.entry.any_rules ?? []),
    ...(draft.exits.rules ?? []),
    ...(draft.cases ?? []).flatMap((item) => [
      ...(item.rules ?? []),
      ...(item.any_rules ?? []),
    ]),
  ];
  const userKeys = new Set(
    user.map((study) => studyKey(study.name, study.params)),
  );
  const added = new Set<string>();
  for (const rule of all) {
    if (rule.type !== "compare") continue;
    if (
      rule.timeframe !== timeframe ||
      (rule.candles ?? "normal") !== candles
    ) {
      found.elsewhere += 1;
      continue;
    }
    const sides = [rule.left, rule.right];
    const fixed = sides.find((side) => side.kind === "value")?.value;
    for (const side of sides) {
      if (side.kind !== "indicator" || !side.name) continue;
      if (PRICE_OPERANDS.has(side.name)) {
        if (fixed !== undefined && fixed !== null)
          found.priceLevels.push(fixed);
        continue;
      }
      const indicator = indicators.get(side.name);
      if (!indicator) continue;
      const params = { ...defaults(indicator), ...(side.params ?? {}) };
      const key = studyKey(side.name, params);
      if (fixed !== undefined && fixed !== null) {
        found.levels.set(key, [
          ...(found.levels.get(key) ?? []),
          { field: side.field ?? null, value: fixed },
        ]);
      }
      if (userKeys.has(key) || added.has(key)) continue;
      added.add(key);
      found.studies.push({
        id: `rule-${key}`,
        name: side.name,
        params,
        strategy: true,
      });
    }
  }
  // Indicators picked for the strategy are drawn too, even before a rule reads them, on the
  // candles they are read on.
  for (const pick of draft.indicators ?? []) {
    const indicator = indicators.get(pick.name);
    if (!indicator || PRICE_OPERANDS.has(pick.name)) continue;
    if (
      (pick.timeframe ?? "1d") !== timeframe ||
      (pick.candles ?? "normal") !== candles
    ) {
      found.elsewhere += 1;
      found.otherPicks.push(pick);
      continue;
    }
    const params = { ...defaults(indicator), ...(pick.params ?? {}) };
    const key = studyKey(pick.name, params);
    if (userKeys.has(key) || added.has(key)) continue;
    added.add(key);
    found.studies.push({
      id: `rule-${key}`,
      name: pick.name,
      params,
      strategy: true,
    });
  }
  return found;
}

/** The timeframe and candles most of the entry rules read, which replay switches to. */
function strategyFrame(
  draft: LabSpec,
): { timeframe: TechTimeframe; candles: CandleType } | null {
  const rules = [
    ...(draft.entry.rules ?? []),
    ...(draft.entry.any_rules ?? []),
    ...(draft.cases ?? []).flatMap((item) => [
      ...(item.rules ?? []),
      ...(item.any_rules ?? []),
    ]),
  ];
  if (!rules.length) return null;
  const counts = new Map<string, number>();
  for (const rule of rules) {
    const key = `${rule.timeframe}|${rule.candles ?? "normal"}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const [best] = [...counts.entries()].sort((a, b) => b[1] - a[1])[0];
  const [timeframe, candles] = best.split("|");
  return {
    timeframe: timeframe as TechTimeframe,
    candles: candles as CandleType,
  };
}

function ruleFor(
  study: StudyState,
  indicator: Indicator,
  timeframe: TechTimeframe,
  candles: CandleType,
): TechRule {
  const field = indicator.fields[0]?.value ?? null;
  const operand = {
    kind: "indicator" as const,
    name: study.name,
    params: study.params,
    field,
  };
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

interface Props {
  catalog: TechCatalog;
  symbols: string[];
  rules: TechRule[];
  anyRules: TechRule[];
  /** Adds a rule built from an indicator on the chart. Absent when rules cannot be added. */
  onAddRule?: (rule: TechRule) => void;
  /** The strategy being built, for replay. Absent where there is nothing to replay. */
  draft?: LabSpec;
  /** Underlyings with option history, which replay needs. */
  tradable?: string[];
  /** Replays these trades, such as a saved backtest's, instead of backtesting the draft. */
  replaySource?: {
    label: string;
    load: (symbol: string) => Promise<ReplayData>;
  };
  /** A preview of the draft: only its own indicators and levels, on the candles its rules
   * read. Indicators added here are for this look only and are not kept. */
  strategyPreview?: boolean;
}

const PREVIEW_KEY = "algoverse.chart.preview.v1";

/** The preview's start: the symbol and range last used there, the strategy's own candles,
 * and no indicators but the strategy's, so one strategy's indicators never show on another. */
function previewStart(symbols: string[], draft: LabSpec | undefined): Saved {
  const base = readSaved(symbols);
  let kept: Partial<Saved> = {};
  try {
    kept = JSON.parse(
      localStorage.getItem(PREVIEW_KEY) ?? "{}",
    ) as Partial<Saved>;
  } catch {
    // A fresh start when storage is blocked.
  }
  // The candles its rules read; without rules, the ones it decides on.
  const frame = draft ? strategyFrame(draft) : null;
  const decides = draft?.decide_every ?? "day";
  const symbol =
    kept.symbol && symbols.includes(kept.symbol) ? kept.symbol : base.symbol;
  return {
    ...base,
    symbol,
    sessions: kept.sessions ?? base.sessions,
    timeframe: frame?.timeframe ?? (decides === "day" ? "1d" : decides),
    candles: frame?.candles ?? "normal",
    studies: [],
    signals: true,
  };
}

/** Weekdays from `iso` to today, an upper bound on the sessions a chart must load. */
function weekdaysSince(iso: string): number {
  const day = new Date(`${iso}T00:00:00Z`);
  const today = new Date();
  let count = 0;
  for (const d = day; d <= today; d.setUTCDate(d.getUTCDate() + 1)) {
    if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) count += 1;
  }
  return count;
}

const REPLAY_RANGES = [
  { sessions: 21, label: "1 month" },
  { sessions: 63, label: "3 months" },
  { sessions: 125, label: "6 months" },
  { sessions: 250, label: "1 year" },
];

export function IndexChart({
  catalog,
  symbols,
  rules,
  anyRules,
  onAddRule,
  draft,
  tradable,
  replaySource,
  strategyPreview = false,
}: Props) {
  const [saved, setSaved] = useState<Saved>(() =>
    strategyPreview ? previewStart(symbols, draft) : readSaved(symbols),
  );
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
  // Replay: the draft's backtest, played one candle at a time up to `cursorTime`.
  const [replay, setReplay] = useState<ReplayData | null>(null);
  const [replayBusy, setReplayBusy] = useState(false);
  const [replayError, setReplayError] = useState<string | null>(null);
  const [replaySessions, setReplaySessions] = useState(21);
  const [cursorTime, setCursorTime] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  // The request the shown data answers, so replay waits for data covering its window.
  const [loadedKey, setLoadedKey] = useState("");
  const themeTick = useThemeTick();

  const box = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLDivElement>(null);
  // Full screen: the browser's own where it is allowed, otherwise the chart fills the window.
  const [full, setFull] = useState<"off" | "screen" | "window">("off");
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<SeriesType>[]>([]);
  const timesRef = useRef<Map<number, number>>(new Map());
  const pickerRef = useRef<HTMLDivElement>(null);

  const indicators = useMemo(
    () => new Map(catalog.indicators.map((i) => [i.name, i])),
    [catalog],
  );
  const update = (change: Partial<Saved>) =>
    setSaved((current) => ({ ...current, ...change }));

  useEffect(() => {
    try {
      if (strategyPreview) {
        // Only the underlying and ranges are kept; candles and indicators come from the strategy.
        const { symbol: kept, sessions: ranges } = saved;
        localStorage.setItem(
          PREVIEW_KEY,
          JSON.stringify({ symbol: kept, sessions: ranges }),
        );
      } else {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
      }
    } catch {
      // Settings only last for this visit when storage is blocked.
    }
  }, [saved, strategyPreview]);

  // ---- Data ----------------------------------------------------------------------------------
  const showStrategy = signals || replay !== null;
  const strategy = useMemo(
    () =>
      showStrategy
        ? strategyLines(draft, indicators, timeframe, candles, studies)
        : null,
    // The draft is rebuilt on every render, so its rules and indicators go by content.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      showStrategy,
      JSON.stringify([
        draft?.entry,
        draft?.exits,
        draft?.cases,
        draft?.indicators,
      ]),
      indicators,
      timeframe,
      candles,
      studies,
    ],
  );
  const shown = useMemo(
    () => [...studies, ...(strategy?.studies ?? [])],
    [studies, strategy],
  );

  const requestKey = JSON.stringify({
    symbol,
    timeframe,
    candles,
    sessions,
    studies: shown.map(({ id, name, params }) => ({ id, name, params })),
    rules: signals ? rules : [],
    anyRules: signals ? anyRules : [],
    cases: signals
      ? (draft?.cases ?? []).map((item) => ({
          name: item.name.trim() || "Case",
          bias: item.bias,
          rules: item.rules ?? [],
          any_rules: item.any_rules ?? [],
        }))
      : [],
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
            cases: body.cases,
          },
          controller.signal,
        )
        .then((next) => {
          setData(next);
          setLoadedKey(requestKey);
          setError(null);
          setHover(null);
        })
        .catch((reason) => {
          if (controller.signal.aborted) return;
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load the chart.",
          );
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
      handleScroll: {
        mouseWheel: false,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false,
      },
      handleScale: {
        mouseWheel: false,
        pinch: true,
        axisPressedMouseMove: true,
      },
    });
    chartRef.current = chart;
    chart.subscribeCrosshairMove((param) => {
      const index =
        param.time === undefined
          ? undefined
          : timesRef.current.get(param.time as number);
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
    const observer = new ResizeObserver(() =>
      window.requestAnimationFrame(measurePanes),
    );
    observer.observe(box.current);
    return () => observer.disconnect();
  }, [measurePanes]);

  // Which pane each separate-pane study draws in, counted from 1.
  const panes = useMemo(() => {
    const found = new Map<string, number>();
    if (!data || data.empty) return found;
    for (const study of data.studies) {
      const state = shown.find((s) => s.id === study.id);
      if (!state || state.hidden || study.error) continue;
      if (study.lines.some((line) => !line.overlay))
        found.set(study.id, found.size + 1);
    }
    return found;
  }, [data, shown]);

  const colours = useMemo(() => {
    const found = new Map<string, string>();
    studies.forEach((study, index) =>
      found.set(study.id, PALETTE[index % PALETTE.length]),
    );
    // The strategy's own lines share one colour, so they read as one thing.
    for (const study of strategy?.studies ?? [])
      found.set(study.id, "--accent");
    return found;
  }, [studies, strategy]);

  const times = useMemo(() => (data && !data.empty ? data.time : []), [data]);
  const cursor =
    replay && cursorTime !== null && times.length
      ? Math.max(0, indexAt(times, cursorTime))
      : null;
  const replayLine = useMemo(
    () => (replay && times.length ? timeline(times, replay) : null),
    [replay, times],
  );

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
        panes: {
          separatorColor: css("--border"),
          separatorHoverColor: css("--accent-wash"),
        },
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

    // In replay only the candles up to the cursor exist yet.
    const end = cursor !== null ? cursor + 1 : data.time.length;
    const times = data.time.slice(0, end) as UTCTimestamp[];
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
        const [o, h, l, c] = [
          data.open[i],
          data.high[i],
          data.low[i],
          data.close[i],
        ];
        return o === null || h === null || l === null || c === null
          ? []
          : [{ time, open: o, high: h, low: l, close: c }];
      }),
    );
    seriesRef.current.push(candleSeries);
    for (const level of strategy?.priceLevels ?? []) {
      candleSeries.createPriceLine({
        price: level,
        color: css("--accent"),
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "rule",
      });
    }

    for (const study of data.studies) {
      const state = shown.find((s) => s.id === study.id);
      if (!state || state.hidden || study.error) continue;
      const colour = css(colours.get(study.id) ?? PALETTE[0]);
      const ruleLevels =
        strategy?.levels.get(studyKey(state.name, state.params)) ?? [];
      if (SESSION_BOXES.has(study.name)) {
        // A line series would join one day's range to the next; the box breaks each session.
        const field = (name: string) =>
          study.lines.find((l) => l.field === name)?.values.slice(0, end) ?? [];
        candleSeries.attachPrimitive(
          new BandFill(
            times,
            field("high"),
            field("low"),
            withAlpha(colour, 0.1),
            {
              stroke: colour,
              middle: field("mid"),
            },
          ),
        );
        continue;
      }
      const pane = panes.get(study.id) ?? 0;
      let levelsDrawn = false;
      const band = BANDS[study.name];
      const middleColour = css(
        colours.get(study.id) === "--series-2" ? "--series-1" : "--series-2",
      );
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
            {
              priceLineVisible: false,
              lastValueVisible: false,
              priceFormat: { type: "price", precision: 2, minMove: 0.01 },
            },
            paneIndex,
          );
          bars.setData(
            points.map((p) =>
              "value" in p
                ? {
                    ...p,
                    color:
                      (p.value as number) >= 0
                        ? css("--up-wash")
                        : css("--down-wash"),
                  }
                : p,
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
            lineStyle:
              !band && dashed(line.field) ? LineStyle.Dashed : LineStyle.Solid,
            lineType: STEPPED.has(study.name)
              ? LineType.WithSteps
              : LineType.Simple,
            lineVisible: !DOTTED.has(study.name),
            pointMarkersVisible: DOTTED.has(study.name),
            pointMarkersRadius: 1.5,
            priceLineVisible: false,
            lastValueVisible: band ? Boolean(isMiddle) : index === 0,
            crosshairMarkerVisible: false,
          },
          paneIndex,
        );
        series.setData(points);
        for (const level of ruleLevels) {
          if (level.field !== null && level.field !== line.field) continue;
          if (level.field === null && index > 0) continue;
          series.createPriceLine({
            price: level.value,
            color: css("--accent"),
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: "rule",
          });
        }
        if (band && line.field === band.upper) {
          const lower = study.lines.find((other) => other.field === band.lower);
          if (lower) {
            series.attachPrimitive(
              new BandFill(
                times,
                line.values.slice(0, end),
                lower.values.slice(0, end),
                withAlpha(colour, 0.09),
              ),
            );
          }
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

    if (replay && replayLine) {
      const pnl = chart.addSeries(
        BaselineSeries,
        {
          baseValue: { type: "price", price: 0 },
          topLineColor: css("--pnl-up"),
          topFillColor1: withAlpha(css("--pnl-up"), 0.18),
          topFillColor2: withAlpha(css("--pnl-up"), 0.02),
          bottomLineColor: css("--pnl-down"),
          bottomFillColor1: withAlpha(css("--pnl-down"), 0.02),
          bottomFillColor2: withAlpha(css("--pnl-down"), 0.18),
          lineWidth: 2,
          priceLineVisible: false,
          priceFormat: { type: "price", precision: 0, minMove: 1 },
        },
        panes.size + 1,
      );
      pnl.setData(times.map((time, i) => ({ time, value: replayLine.pnl[i] })));
      seriesRef.current.push(pnl);
      // The open trade's exit levels on the same scale: where the line meets them, it exits.
      for (const [values, colour, title] of [
        [replayLine.stop, css("--pnl-down"), "stop"],
        [replayLine.target, css("--pnl-up"), "target"],
      ] as const) {
        const level = chart.addSeries(
          LineSeries,
          {
            color: colour,
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            lineType: LineType.WithSteps,
            priceLineVisible: false,
            lastValueVisible:
              values[end - 1] !== null && values[end - 1] !== undefined,
            crosshairMarkerVisible: false,
            title,
            priceFormat: { type: "price", precision: 0, minMove: 1 },
            // The target is often far away; it should not squash the P&L line.
            autoscaleInfoProvider: title === "target" ? () => null : undefined,
          },
          panes.size + 1,
        );
        level.setData(
          times.map((time, i) => {
            const value = values[i];
            return value === null || value === undefined
              ? { time }
              : { time, value };
          }),
        );
        seriesRef.current.push(level);
      }
    }

    const all = chart.panes();
    if (all.length > 1) {
      all[0].setStretchFactor(PRICE_HEIGHT / PANE_HEIGHT);
      for (const pane of all.slice(1)) pane.setStretchFactor(1);
    }

    if (replay && replayLine) {
      const markers = replay.trades.flatMap((trade, i) => {
        const entry = replayLine.entries[i];
        const exit = replayLine.exits[i];
        const found = [];
        if (entry >= 0 && entry < end) {
          found.push({
            time: data.time[entry] as Time,
            position: "belowBar" as const,
            color: css("--accent"),
            shape: "arrowUp" as const,
            text:
              trade.legs.length === 1
                ? `${trade.legs[0].side === "buy" ? "Buy" : "Sell"} ${trade.legs[0].strike} ${trade.legs[0].option_type}`
                : `Open ${trade.legs.length} legs`,
          });
        }
        if (
          exit >= 0 &&
          exit < end &&
          trade.close_time <= (data.time[end] ?? Number.POSITIVE_INFINITY)
        ) {
          found.push({
            time: data.time[exit] as Time,
            position: "aboveBar" as const,
            color: trade.net_pnl >= 0 ? css("--pnl-up") : css("--pnl-down"),
            shape: "circle" as const,
            text: `${trade.net_pnl >= 0 ? "+" : "−"}₹${Math.abs(Math.round(trade.net_pnl)).toLocaleString("en-IN")}`,
          });
        }
        return found;
      });
      markers.sort((a, b) => (a.time as number) - (b.time as number));
      createSeriesMarkers(candleSeries, markers);
    } else if (signals) {
      // The first case's hits point up; a bearish further case's point down from above.
      const shown = data.signals.hits
        .filter((i) => i < end)
        .map((i) => ({
          time: times[i] as Time,
          position: "belowBar" as const,
          color: css("--accent"),
          shape: "arrowUp" as const,
        }));
      for (const item of data.signals.cases ?? []) {
        const down = item.bias === "bearish";
        for (const i of item.hits) {
          if (i >= end) continue;
          shown.push({
            time: times[i] as Time,
            position: (down ? "aboveBar" : "belowBar") as "belowBar",
            color: css("--accent"),
            shape: (down ? "arrowDown" : "arrowUp") as "arrowUp",
          });
        }
      }
      if (shown.length) {
        shown.sort((a, b) => (a.time as number) - (b.time as number));
        createSeriesMarkers(candleSeries, shown);
      }
    }
    window.requestAnimationFrame(measurePanes);
    if (cursor !== null && playing) chart.timeScale().scrollToRealTime();
  }, [
    data,
    shown,
    strategy,
    panes,
    colours,
    signals,
    timeframe,
    themeTick,
    measurePanes,
    cursor,
    replay,
    replayLine,
    playing,
  ]);

  // ---- Replay --------------------------------------------------------------------------------
  const startReplay = async () => {
    if (!draft) return;
    setReplayBusy(true);
    setReplayError(null);
    setPlaying(false);
    try {
      const result = replaySource
        ? await replaySource.load(symbol)
        : await api.labReplay({
            spec: draft,
            symbol,
            sessions: replaySessions,
          });
      const needed = replaySource
        ? weekdaysSince(result.start)
        : replaySessions;
      // Show the candles the strategy decides on, so its lines are drawn as it reads them.
      const frame = strategyFrame(draft);
      const target = frame?.timeframe ?? timeframe;
      const change: Partial<Saved> = frame
        ? { timeframe: frame.timeframe, candles: frame.candles }
        : {};
      // Minute charts must load the whole replay window.
      const loaded = saved.sessions?.[target] ?? DEFAULT_RANGE[target] ?? 21;
      if (MINUTES.has(target) && loaded < needed) {
        const fitting =
          RANGES.find((r) => r.sessions >= needed) ?? RANGES[RANGES.length - 1];
        change.sessions = { ...saved.sessions, [target]: fitting.sessions };
      }
      if (Object.keys(change).length) update(change);
      setReplay(result);
      setCursorTime(null);
    } catch (reason) {
      setReplayError(
        reason instanceof Error ? reason.message : "The replay could not run.",
      );
    } finally {
      setReplayBusy(false);
    }
  };
  const exitReplay = () => {
    setReplay(null);
    setCursorTime(null);
    setPlaying(false);
  };

  // Start at the first candle of the replay window, with some history in view. A minute chart
  // that does not reach back to the window loads the next longer range first.
  useEffect(() => {
    if (
      !replay ||
      cursorTime !== null ||
      !times.length ||
      loadedKey !== requestKey
    )
      return;
    const first = Date.parse(`${replay.start}T00:00:00Z`) / 1000;
    if (MINUTES.has(timeframe) && times[0] > first) {
      const longer = RANGES.find((r) => r.sessions > (sessions ?? 0));
      if (longer) {
        update({
          sessions: { ...saved.sessions, [timeframe]: longer.sessions },
        });
        return;
      }
    }
    const start = Math.max(0, indexAt(times, first - 1) + 1);
    setCursorTime(times[Math.min(start, times.length - 1)]);
    chartRef.current?.timeScale().setVisibleLogicalRange({
      from: Math.max(0, start - 120),
      to: start + 20,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replay, cursorTime, times, loadedKey, requestKey]);

  const moveTo = useCallback(
    (index: number) => {
      if (!times.length) return;
      setCursorTime(times[Math.max(0, Math.min(times.length - 1, index))]);
    },
    [times],
  );

  useEffect(() => {
    if (!playing || cursor === null) return;
    if (cursor >= times.length - 1) {
      setPlaying(false);
      return;
    }
    const timer = window.setTimeout(() => moveTo(cursor + 1), 1000 / speed);
    return () => window.clearTimeout(timer);
  }, [playing, cursor, speed, times, moveTo]);

  const nextEntry =
    replay && replayLine && cursor !== null
      ? replayLine.entries
          .filter((entry) => entry > cursor)
          .sort((a, b) => a - b)[0]
      : undefined;

  // A new symbol or timeframe starts at the latest candles.
  const lastFit = useRef("");
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !data || data.empty) return;
    const key = `${data.symbol}/${data.timeframe}/${data.time.length}`;
    if (lastFit.current === key) return;
    lastFit.current = key;
    const count = data.time.length;
    chart.timeScale().setVisibleLogicalRange({
      from: Math.max(0, count - 160),
      to: count + 4,
    });
  }, [data]);

  const zoom = (factor: number) => {
    const scale = chartRef.current?.timeScale();
    const range = scale?.getVisibleLogicalRange();
    if (!scale || !range) return;
    const width = Math.max(10, (range.to - range.from) * factor);
    scale.setVisibleLogicalRange({ from: range.to - width, to: range.to });
  };

  // ---- Full screen ---------------------------------------------------------------------------
  useEffect(() => {
    const onChange = () => {
      const active =
        document.fullscreenElement !== null &&
        document.fullscreenElement === root.current;
      setFull((current) =>
        active ? "screen" : current === "screen" ? "off" : current,
      );
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  useEffect(() => {
    if (full !== "window") return;
    const onKey = (event: KeyboardEvent) =>
      event.key === "Escape" && setFull("off");
    document.addEventListener("keydown", onKey);
    document.body.classList.add("chart-full-open");
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.classList.remove("chart-full-open");
    };
  }, [full]);

  // The panes change size, so the legends follow.
  useEffect(() => {
    window.requestAnimationFrame(measurePanes);
  }, [full, measurePanes]);

  const toggleFull = async () => {
    if (full === "screen") {
      await document.exitFullscreen().catch(() => undefined);
      return;
    }
    if (full === "window") {
      setFull("off");
      return;
    }
    const element = root.current;
    if (element?.requestFullscreen) {
      // Some embedded browsers neither grant nor refuse, so wait briefly, then fill the window.
      const granted = await Promise.race([
        element.requestFullscreen().then(
          () => true,
          () => false,
        ),
        new Promise<boolean>((resolve) =>
          window.setTimeout(() => resolve(false), 600),
        ),
      ]);
      if (granted || document.fullscreenElement === element) return;
    }
    setFull("window");
  };

  // ---- Picker --------------------------------------------------------------------------------
  useEffect(() => {
    if (!pickerOpen) return;
    const close = (event: MouseEvent) => {
      if (
        pickerRef.current &&
        !pickerRef.current.contains(event.target as Node)
      )
        setPickerOpen(false);
    };
    const escape = (event: KeyboardEvent) =>
      event.key === "Escape" && setPickerOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [pickerOpen]);

  const addStudy = (indicator: Indicator) => {
    if (studies.length >= MAX_STUDIES) return;
    update({
      studies: [
        ...studies,
        { id: newId(), name: indicator.name, params: defaults(indicator) },
      ],
    });
    setPickerOpen(false);
    setSearch("");
  };
  const setStudy = (id: string, change: Partial<StudyState>) =>
    update({
      studies: studies.map((s) => (s.id === id ? { ...s, ...change } : s)),
    });
  const removeStudy = (id: string) => {
    update({ studies: studies.filter((s) => s.id !== id) });
    if (editing === id) setEditing(null);
  };

  const query = search.trim().toLowerCase();
  const choices = catalog.indicators.filter(
    (i) =>
      !HIDDEN_FROM_PICKER.has(i.name) &&
      (!query ||
        i.label.toLowerCase().includes(query) ||
        i.name.includes(query) ||
        i.description.toLowerCase().includes(query)),
  );
  const groups = byType(catalog, choices, (i) => i.name);

  // ---- Legend --------------------------------------------------------------------------------
  const count = data && !data.empty ? data.time.length : 0;
  const at = hover ?? (count ? count - 1 : null);
  const ohlc =
    at !== null && data && !data.empty
      ? {
          o: data.open[at],
          h: data.high[at],
          l: data.low[at],
          c: data.close[at],
          prev: at > 0 ? data.close[at - 1] : null,
        }
      : null;
  const change =
    ohlc && ohlc.c !== null && ohlc.prev
      ? ((ohlc.c - ohlc.prev) / ohlc.prev) * 100
      : null;
  const studyData = new Map((data?.studies ?? []).map((s) => [s.id, s]));
  const editingStudy = studies.find((s) => s.id === editing);
  const editingIndicator = editingStudy
    ? indicators.get(editingStudy.name)
    : undefined;

  const legendRow = (state: StudyState) => {
    const study = studyData.get(state.id);
    const indicator = indicators.get(state.name);
    const short = indicator?.params
      .map((p) => state.params[p.name] ?? p.default)
      .join(", ");
    const values =
      study?.lines.map((line) => (at === null ? null : line.values[at])) ?? [];
    return (
      <div
        key={state.id}
        className={`chart-legend-row${state.hidden ? " is-hidden" : ""}`}
      >
        <span
          className="chart-swatch"
          style={{ background: `var(${colours.get(state.id)})` }}
          aria-hidden
        />
        <span className="chart-legend-name" title={indicator?.label}>
          {SHORT[state.name] ?? indicator?.label ?? state.name}
          {short ? ` ${short}` : ""}
        </span>
        {state.strategy && <span className="chart-legend-tag">strategy</span>}
        {study?.error ? (
          <span className="chart-legend-error">{study.error}</span>
        ) : (
          !state.hidden && (
            <span className="chart-legend-values">
              {values.map((v, i) => (
                <span key={i}>{priceText(v)}</span>
              ))}
            </span>
          )
        )}
        {!state.strategy && (
          <span className="chart-legend-tools">
            <button
              type="button"
              className="chart-tool"
              onClick={() => setStudy(state.id, { hidden: !state.hidden })}
              aria-label={
                state.hidden
                  ? `Show ${indicator?.label}`
                  : `Hide ${indicator?.label}`
              }
              title={state.hidden ? "Show" : "Hide"}
            >
              {state.hidden ? (
                <EyeOff size={14} aria-hidden />
              ) : (
                <Eye size={14} aria-hidden />
              )}
            </button>
            {indicator && indicator.params.length > 0 && (
              <button
                type="button"
                className="chart-tool"
                aria-label={`Settings for ${indicator.label}`}
                title="Settings"
                aria-expanded={editing === state.id}
                onClick={() =>
                  setEditing(editing === state.id ? null : state.id)
                }
              >
                <Settings size={14} aria-hidden />
              </button>
            )}
            {onAddRule && indicator && !study?.error && (
              <button
                type="button"
                className="chart-tool"
                aria-label={`Add an entry rule using ${indicator.label}`}
                title="Add as an entry rule"
                onClick={() =>
                  onAddRule(ruleFor(state, indicator, timeframe, candles))
                }
              >
                <Plus size={14} aria-hidden />
              </button>
            )}
            <button
              type="button"
              className="chart-tool"
              aria-label={`Remove ${indicator?.label}`}
              title="Remove"
              onClick={() => removeStudy(state.id)}
            >
              <X size={14} aria-hidden />
            </button>
          </span>
        )}
      </div>
    );
  };

  const overlayStudies = shown.filter((s) => !panes.has(s.id));
  const signalInfo = data && !data.empty && signals ? data.signals : null;
  const height = PRICE_HEIGHT + PANE_HEIGHT * (panes.size + (replay ? 1 : 0));
  const replayable = (tradable ?? symbols).includes(symbol);
  const replayHint = `Replay needs option history, kept for ${(tradable ?? []).join(", ")}.`;

  return (
    <div
      ref={root}
      className={`index-chart${full !== "off" ? " is-full" : ""}${full === "window" ? " is-full-window" : ""}`}
    >
      <div className="chart-toolbar" role="toolbar" aria-label="Chart settings">
        <label className="chart-symbol">
          <span className="visually-hidden">Index</span>
          <select
            value={symbol}
            onChange={(e) => update({ symbol: e.target.value })}
          >
            {symbols.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <div className="chart-group" role="group" aria-label="Timeframe">
          {TIMEFRAMES.map((t) => (
            <ChartChip
              key={t.value}
              pressed={timeframe === t.value}
              onClick={() => update({ timeframe: t.value })}
            >
              {t.label}
            </ChartChip>
          ))}
        </div>
        {sessions !== undefined && (
          <label className="chart-range">
            <span className="visually-hidden">Range</span>
            <select
              value={sessions}
              title="How many sessions to load"
              onChange={(e) =>
                update({
                  sessions: {
                    ...saved.sessions,
                    [timeframe]: Number(e.target.value),
                  },
                })
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
          {(catalog.candle_types ?? [{ value: "normal", label: "Normal" }]).map(
            (t) => (
              <ChartChip
                key={t.value}
                pressed={candles === t.value}
                onClick={() => update({ candles: t.value })}
              >
                {t.value === "normal" ? "Candles" : t.label}
              </ChartChip>
            ),
          )}
        </div>
        <div className="chart-picker" ref={pickerRef}>
          <ChartChip
            className="chart-add"
            aria-expanded={pickerOpen}
            aria-haspopup="dialog"
            disabled={studies.length >= MAX_STUDIES}
            onClick={() => setPickerOpen(!pickerOpen)}
          >
            <ChartLine size={14} aria-hidden /> Indicators
          </ChartChip>
          {pickerOpen && (
            <div
              className="chart-picker-panel"
              role="dialog"
              aria-label="Add an indicator"
            >
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
                      const blocked =
                        indicator.intraday_only && !MINUTES.has(timeframe);
                      return (
                        <IndicatorTip
                          key={indicator.name}
                          indicator={indicator}
                          className="indicator-tip-row"
                        >
                          <button
                            type="button"
                            className="chart-picker-item"
                            onClick={() => addStudy(indicator)}
                          >
                            <span>{indicator.label}</span>
                            {(indicator.description || blocked) && (
                              <small>
                                {blocked
                                  ? "Minute candles only"
                                  : indicator.description}
                              </small>
                            )}
                          </button>
                        </IndicatorTip>
                      );
                    })}
                  </div>
                ))}
                {!groups.length && (
                  <p className="chart-picker-empty">
                    No indicator matches "{search}".
                  </p>
                )}
              </div>
            </div>
          )}
        </div>
        {!strategyPreview && (
          <label className="chart-toggle">
            <input
              type="checkbox"
              checked={signals}
              onChange={(e) => update({ signals: e.target.checked })}
            />
            <span title="Draw the indicators and levels the strategy's rules read, and mark candles where its entry rules held">
              Strategy
            </span>
          </label>
        )}
        <div className="chart-group" role="group" aria-label="Zoom">
          <ChartChip
            onClick={() => zoom(0.6)}
            aria-label="Zoom in"
            title="Zoom in"
          >
            +
          </ChartChip>
          <ChartChip
            onClick={() => zoom(1.6)}
            aria-label="Zoom out"
            title="Zoom out"
          >
            −
          </ChartChip>
          <ChartChip
            onClick={() => chartRef.current?.timeScale().fitContent()}
            title="Show all loaded candles"
          >
            Fit
          </ChartChip>
        </div>
        <ChartChip
          className="chart-add"
          onClick={toggleFull}
          pressed={full !== "off"}
          title={
            full !== "off"
              ? "Exit full screen (Esc)"
              : "Show the chart full screen"
          }
        >
          {full !== "off" ? (
            <Minimize2 size={14} aria-hidden />
          ) : (
            <Maximize2 size={14} aria-hidden />
          )}
          {full !== "off" ? "Exit full screen" : "Full screen"}
        </ChartChip>
        {draft && !replay && (
          <div
            className="chart-group replay-start"
            role="group"
            aria-label="Replay the draft"
          >
            {!replaySource && (
              <label className="chart-range">
                <span className="visually-hidden">Replay period</span>
                <select
                  value={replaySessions}
                  disabled={replayBusy}
                  onChange={(e) => setReplaySessions(Number(e.target.value))}
                  title="How far back to replay"
                >
                  {REPLAY_RANGES.map((r) => (
                    <option key={r.sessions} value={r.sessions}>
                      {r.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <ChartChip
              className="chart-add"
              disabled={replayBusy || !replayable}
              onClick={startReplay}
              title={
                replayable
                  ? "Backtest the draft and play its trades candle by candle"
                  : replayHint
              }
            >
              {replayBusy
                ? "Preparing…"
                : replaySource
                  ? `▶ Replay ${replaySource.label}`
                  : "▶ Replay"}
            </ChartChip>
          </div>
        )}
      </div>

      {replayBusy && replaySource && (
        <p className="replay-status" role="status">
          Loading the backtest's trades on {symbol} and how each position's
          value moved.
        </p>
      )}
      {replayBusy && !replaySource && (
        <p className="replay-status" role="status">
          Backtesting the draft on {symbol} over the last{" "}
          {REPLAY_RANGES.find((r) => r.sessions === replaySessions)?.label}.
          {draft?.decide_every && draft.decide_every !== "day"
            ? " Minute decisions take about half a minute a month."
            : " This takes a few seconds."}
        </p>
      )}
      {replayError && <Alert tone="critical">{replayError}</Alert>}
      {replay && cursor !== null && (
        <ReplayControls
          playing={playing}
          speed={speed}
          atStart={cursor <= 0}
          atEnd={cursor >= times.length - 1}
          hasNextTrade={nextEntry !== undefined}
          label={wallText(times[cursor], MINUTES.has(timeframe))}
          onPlay={() => setPlaying(!playing)}
          onStep={(by) => {
            setPlaying(false);
            moveTo(cursor + by);
          }}
          onRestart={() => {
            setPlaying(false);
            setCursorTime(null);
          }}
          onNextTrade={() => {
            if (nextEntry === undefined) return;
            setPlaying(false);
            moveTo(nextEntry);
            chartRef.current?.timeScale().setVisibleLogicalRange({
              from: Math.max(0, nextEntry - 80),
              to: nextEntry + 20,
            });
          }}
          onSpeed={setSpeed}
          onExit={exitReplay}
        />
      )}

      {editingStudy && editingIndicator && (
        <div
          className="chart-settings"
          role="group"
          aria-label={`${editingIndicator.label} settings`}
        >
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
                  const value = Math.min(
                    param.max,
                    Math.max(param.min, param.whole ? Math.round(raw) : raw),
                  );
                  setStudy(editingStudy.id, {
                    params: { ...editingStudy.params, [param.name]: value },
                  });
                }}
              />
            </label>
          ))}
          <ChartChip onClick={() => setEditing(null)}>Done</ChartChip>
        </div>
      )}

      <div
        className="chart-stage"
        style={full !== "off" ? { minHeight: height } : { height }}
      >
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
                O <b>{priceText(ohlc.o)}</b> H <b>{priceText(ohlc.h)}</b> L{" "}
                <b>{priceText(ohlc.l)}</b> C <b>{priceText(ohlc.c)}</b>
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
          const state = shown.find((s) => s.id === id);
          return state ? (
            <div
              key={id}
              className="chart-legend"
              style={{ top: (paneTops[pane] ?? 0) + 4 }}
            >
              {legendRow(state)}
            </div>
          ) : null;
        })}
        {(error || data?.empty) && (
          <div className="chart-empty">{error ?? data?.empty}</div>
        )}
      </div>

      {replay && cursor !== null && (
        <ReplayPanel
          data={replay}
          times={times}
          cursor={cursor}
          intradayTimes={replay.intraday}
        />
      )}

      <div className="chart-foot">
        {strategyPreview && (strategy?.otherPicks.length ?? 0) > 0 && (
          <span className="chart-other-picks">
            On other candles:{" "}
            {strategy?.otherPicks.map((pick, index) => (
              <ChartChip
                key={index}
                title="Show the candles this indicator reads"
                onClick={() =>
                  update({
                    timeframe: pick.timeframe ?? "1d",
                    candles: pick.candles ?? "normal",
                  })
                }
              >
                {pickText(catalog, pick)}
              </ChartChip>
            ))}
          </span>
        )}
        {data?.source && (
          <span className="muted">
            {data.source}
            {data.first ? `, from ${data.first}` : ""}. Indicators use the same
            code as backtests.
          </span>
        )}
        {signalInfo && (
          <div className="chart-signals">
            {signalInfo.rules.length ? (
              <span>
                <span className="chart-signal-mark" aria-hidden>
                  ▲
                </span>{" "}
                Entry rules held on <strong>{signalInfo.hits.length}</strong> of{" "}
                {count} candles shown.
                {signalInfo.rules.map((rule) => (
                  <span
                    key={`${rule.group}-${rule.text}`}
                    className="chart-signal-rule"
                  >
                    {rule.group === "any" ? "Any of: " : ""}
                    {rule.text}: {rule.count}
                  </span>
                ))}
              </span>
            ) : rules.length + anyRules.length ? (
              <span className="muted">No entry rule uses this timeframe.</span>
            ) : draft ? (
              <span className="muted">
                Add entry rules to see where they would have held.
              </span>
            ) : null}
            {(signalInfo.cases ?? []).map((item) =>
              item.rules.length ? (
                <span key={item.name}>
                  <span className="chart-signal-mark" aria-hidden>
                    {item.bias === "bearish" ? "▼" : "▲"}
                  </span>{" "}
                  {item.name} held on <strong>{item.hits.length}</strong> of{" "}
                  {count} candles shown.
                  {item.rules.map((rule) => (
                    <span
                      key={`${rule.group}-${rule.text}`}
                      className="chart-signal-rule"
                    >
                      {rule.text}: {rule.count}
                    </span>
                  ))}
                </span>
              ) : null,
            )}
            {signalInfo.other_timeframes.length > 0 && (
              <span className="muted">
                Not shown, other timeframes:{" "}
                {signalInfo.other_timeframes.join("; ")}.
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

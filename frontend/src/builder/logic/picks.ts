import type {
  CandleType,
  IndicatorPick,
  TechCatalog,
  TechOperand,
  TechTimeframe,
} from "../../api";

export type Indicator = TechCatalog["indicators"][number];

export const MAX_RULES = 8;
export const MINUTE_FRAMES = ["1m", "5m", "15m", "1h"];
/** The Current and Previous candle indicators: the candle itself, on the interval picked. */
export const CANDLE_NAMES = ["candle", "prev_candle"];
/** The candle's own prices. Every rule may use them; other indicators are picked first. */
export const PRICE_NAMES = [
  "close",
  "open",
  "high",
  "low",
  "prev_close",
  "prev_high",
  "prev_low",
];

/** Candle patterns are picked under Indicators too, listed as their own type. */
export const PATTERN_GROUP = "Candle patterns";

export function patternOf(
  catalog: TechCatalog,
  name: string | null | undefined,
) {
  return catalog.patterns.find((item) => item.name === name);
}

function patternIndicator(pattern: TechCatalog["patterns"][number]): Indicator {
  return {
    name: pattern.name,
    label: pattern.label,
    unit: "",
    description: pattern.description,
    // How far back it looks is set here, on the pattern, not in each rule.
    params: [
      {
        name: "within",
        label: "Within the last N candles",
        default: 1,
        min: 1,
        max: 20,
        whole: true,
      },
    ],
    fields: [],
    group: PATTERN_GROUP,
  };
}

/** An indicator, or a candle pattern described as one, by name. */
export function indicatorOf(
  catalog: TechCatalog,
  name: string | null | undefined,
): Indicator | undefined {
  const found = catalog.indicators.find((item) => item.name === name);
  if (found) return found;
  const pattern = patternOf(catalog, name);
  return pattern && patternIndicator(pattern);
}

/** Everything the Indicators section offers: the indicators, then the candle patterns. */
export function pickables(catalog: TechCatalog): Indicator[] {
  return [...catalog.indicators, ...catalog.patterns.map(patternIndicator)];
}

/** The pick a pattern rule reads: the pattern and how far back, on the rule's candles. */
export function patternPick(rule: {
  pattern: string;
  within: number;
  timeframe: TechTimeframe;
  candles?: CandleType;
}): IndicatorPick {
  return {
    name: rule.pattern,
    params: { within: rule.within },
    timeframe: rule.timeframe,
    candles: rule.candles ?? "normal",
  };
}

/** Items grouped by indicator type, the types and their members in the catalog's order.
 * `nameOf` says which indicator each item is; types with no items are left out. */
export function byType<T>(
  catalog: TechCatalog,
  items: T[],
  nameOf: (item: T) => string,
): { title: string; items: T[] }[] {
  const groups = [
    ...(catalog.indicator_groups ?? []),
    { name: PATTERN_GROUP, indicators: catalog.patterns.map((p) => p.name) },
  ];
  const place = new Map<string, [number, number]>();
  groups.forEach((group, g) =>
    group.indicators.forEach((name, i) => place.set(name, [g, i])),
  );
  const found = new Map<number, T[]>();
  for (const item of items) {
    const [g] = place.get(nameOf(item)) ?? [groups.length, 0];
    found.set(g, [...(found.get(g) ?? []), item]);
  }
  return [...found.entries()]
    .sort(([a], [b]) => a - b)
    .map(([g, members]) => ({
      title: groups[g]?.name ?? "Other",
      items: [...members].sort(
        (a, b) =>
          (place.get(nameOf(a))?.[1] ?? 0) - (place.get(nameOf(b))?.[1] ?? 0),
      ),
    }));
}

/** A pick with every setting filled in, so "EMA" and "EMA, period 20, daily candles" are the
 * same one. */
export function normalPick(
  catalog: TechCatalog,
  name: string,
  params?: Record<string, number>,
  rest: Partial<IndicatorPick> = {},
): IndicatorPick {
  const indicator = indicatorOf(catalog, name);
  return {
    ...rest,
    name,
    params: Object.fromEntries(
      (indicator?.params ?? []).map((p) => [
        p.name,
        params?.[p.name] ?? p.default,
      ]),
    ),
    timeframe: rest.timeframe ?? "1d",
    candles: rest.candles ?? "normal",
  };
}

/** Which indicator this is: its type, settings and candles. Its name is only a label. */
export function pickKey(catalog: TechCatalog, pick: IndicatorPick): string {
  const full = normalPick(catalog, pick.name, pick.params, pick);
  const params = Object.entries(full.params)
    .map(([key, value]) => `${key}=${value}`)
    .join(",");
  return `${full.name}(${params})@${full.timeframe}/${full.candles}`;
}

/** The pick an operand of a rule reads: its indicator, on the rule's candles. */
export function operandPick(
  operand: TechOperand,
  rule: { timeframe: TechTimeframe; candles?: CandleType },
): IndicatorPick {
  // A side read on other candles than the rule's says which.
  return {
    name: operand.name ?? "",
    params: operand.params ?? {},
    timeframe: operand.timeframe ?? rule.timeframe,
    candles: operand.candles ?? rule.candles ?? "normal",
  };
}

type Frame = { timeframe: TechTimeframe; candles?: CandleType };

/** An operand without candles of its own: it reads the rule's. */
export function onRuleCandles(operand: TechOperand): TechOperand {
  const { timeframe: _t, candles: _c, ...rest } = operand;
  return rest;
}

/** An operand read on `frame`, inside a rule read on `rule`: it names its candles only when
 * they differ from the rule's. Prices and numbers always read the rule's. */
export function readOn(
  operand: TechOperand,
  frame: Frame,
  rule: Frame,
): TechOperand {
  const plain = onRuleCandles(operand);
  if (operand.kind !== "indicator" || PRICE_NAMES.includes(operand.name ?? ""))
    return plain;
  const same =
    frame.timeframe === rule.timeframe &&
    (frame.candles ?? "normal") === (rule.candles ?? "normal");
  return same
    ? plain
    : {
        ...plain,
        timeframe: frame.timeframe,
        candles: frame.candles ?? "normal",
      };
}

const FRAME_SHORT: Record<string, string> = {
  "1m": "1m",
  "5m": "5m",
  "15m": "15m",
  "1h": "1h",
  "1d": "1D",
  "1w": "1W",
  "1mo": "1M",
};

/** Its standard name: "EMA (20), 5m", with "Heikin-Ashi" when read on those candles. */
export function pickStandardText(
  catalog: TechCatalog,
  pick: IndicatorPick,
): string {
  const indicator = indicatorOf(catalog, pick.name);
  if (!indicator) return pick.name;
  const frameText = `${FRAME_SHORT[pick.timeframe ?? "1d"] ?? pick.timeframe}${pick.candles === "heikin_ashi" ? " Heikin-Ashi" : ""}`;
  if (patternOf(catalog, pick.name)) {
    const within = pick.params?.within ?? 1;
    return within > 1
      ? `${indicator.label}, last ${within} candles, ${frameText}`
      : `${indicator.label}, ${frameText}`;
  }
  const params = indicator.params
    .map((p) => pick.params?.[p.name] ?? p.default)
    .join(", ");
  const base = params ? `${indicator.label} (${params})` : indicator.label;
  const frame = FRAME_SHORT[pick.timeframe ?? "1d"] ?? pick.timeframe;
  return `${base}, ${frame}${pick.candles === "heikin_ashi" ? " Heikin-Ashi" : ""}`;
}

/** How the editor lists a pick: the user's name for it, or its standard name. */
export function pickText(catalog: TechCatalog, pick: IndicatorPick): string {
  return pick.label?.trim() || pickStandardText(catalog, pick);
}

/** The operand for a picked indicator: its settings, its first line, the latest candle. */
export function pickedOperand(
  catalog: TechCatalog,
  pick: IndicatorPick,
): TechOperand {
  const full = normalPick(catalog, pick.name, pick.params);
  return {
    kind: "indicator",
    name: full.name,
    params: full.params,
    field: indicatorOf(catalog, pick.name)?.fields[0]?.value ?? null,
    bars_ago: 0,
  };
}

/** An indicator operand with its default settings. */
export function freshIndicator(
  catalog: TechCatalog,
  name: string,
): TechOperand {
  const indicator = indicatorOf(catalog, name);
  return {
    kind: "indicator",
    name,
    params: Object.fromEntries(
      (indicator?.params ?? []).map((p) => [p.name, p.default]),
    ),
    field: indicator?.fields[0]?.value ?? null,
    bars_ago: 0,
  };
}

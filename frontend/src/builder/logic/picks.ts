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

export function indicatorOf(
  catalog: TechCatalog,
  name: string | null | undefined,
): Indicator | undefined {
  return catalog.indicators.find((item) => item.name === name);
}

/** Items grouped by indicator type, the types and their members in the catalog's order.
 * `nameOf` says which indicator each item is; types with no items are left out. */
export function byType<T>(
  catalog: TechCatalog,
  items: T[],
  nameOf: (item: T) => string,
): { title: string; items: T[] }[] {
  const groups = catalog.indicator_groups ?? [];
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
  return {
    name: operand.name ?? "",
    params: operand.params ?? {},
    timeframe: rule.timeframe,
    candles: rule.candles ?? "normal",
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

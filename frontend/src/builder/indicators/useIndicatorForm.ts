import { useMemo, useState } from "react";
import type {
  CandleType,
  IndicatorPick,
  TechCatalog,
  TechTimeframe,
} from "../../api";
import {
  byType,
  MINUTE_FRAMES,
  normalPick,
  pickKey,
  pickText,
  PRICE_NAMES,
  type Indicator,
} from "../logic/picks";

/** The settings as editable text, filled with the pick's values or the defaults. */
function texts(
  catalog: TechCatalog,
  pick: IndicatorPick | null,
): Record<string, string> {
  if (!pick) return {};
  const full = normalPick(catalog, pick.name, pick.params);
  return Object.fromEntries(
    Object.entries(full.params).map(([key, value]) => [key, String(value)]),
  );
}

export type IndicatorFormOptions = {
  catalog: TechCatalog;
  /** The strategy's indicators now, to refuse a second copy with the same settings. */
  picks: IndicatorPick[];
  /** The indicator being changed, or null to add one. */
  editing: IndicatorPick | null;
  /** Only a strategy that decides through the day can read 5-minute to hourly candles. */
  intraday: boolean;
  /** The interval a new indicator starts on: the one the strategy decides on. */
  defaultFrame: TechTimeframe;
};

/** What the indicator form is choosing and setting, and what stops it saving. */
export function useIndicatorForm({
  catalog,
  picks,
  editing,
  intraday,
  defaultFrame,
}: IndicatorFormOptions) {
  const [query, setQuery] = useState("");
  const [name, setName] = useState(editing?.name ?? "");
  const [values, setValues] = useState<Record<string, string>>(() =>
    texts(catalog, editing),
  );
  const [tried, setTried] = useState(false);
  const [label, setLabel] = useState(editing?.label ?? "");
  const [timeframe, setTimeframe] = useState<TechTimeframe>(
    editing?.timeframe ?? defaultFrame,
  );
  const [candles, setCandles] = useState<CandleType>(
    editing?.candles ?? "normal",
  );

  const frames = catalog.timeframes.filter(
    (frame) =>
      intraday ||
      !MINUTE_FRAMES.includes(frame.value) ||
      frame.value === timeframe,
  );
  const offered = useMemo(
    () => catalog.indicators.filter((item) => !PRICE_NAMES.includes(item.name)),
    [catalog],
  );
  const needle = query.trim().toLowerCase();
  const groups = byType(
    catalog,
    offered.filter((item) =>
      `${item.label} ${item.name} ${item.description}`
        .toLowerCase()
        .includes(needle),
    ),
    (item: Indicator) => item.name,
  );
  const chosen = offered.find((item) => item.name === name);

  const choose = (item: Indicator) => {
    setName(item.name);
    setValues(texts(catalog, { name: item.name, params: {} }));
    // Intraday-only indicators, such as the opening range, start on minute candles.
    if (item.intraday_only && !MINUTE_FRAMES.includes(timeframe) && intraday) {
      setTimeframe(MINUTE_FRAMES.includes(defaultFrame) ? defaultFrame : "15m");
    }
    setTried(false);
  };

  // Each setting's problem, if any, and the pick the form describes.
  const problems: Record<string, string> = {};
  const params: Record<string, number> = {};
  for (const param of chosen?.params ?? []) {
    const raw = values[param.name] ?? "";
    const value = Number(raw);
    if (raw.trim() === "" || !Number.isFinite(value))
      problems[param.name] = "Enter a number.";
    else if (value < param.min || value > param.max)
      problems[param.name] = `Use ${param.min} to ${param.max}.`;
    else if (param.whole && !Number.isInteger(value))
      problems[param.name] = "Use a whole number.";
    else params[param.name] = value;
  }
  const valid = !Object.keys(problems).length;
  const pick = chosen
    ? normalPick(catalog, chosen.name, params, {
        label: label.trim() || null,
        timeframe,
        candles,
      })
    : null;
  const ownKey = editing ? pickKey(catalog, editing) : null;
  const others = picks.filter((other) => pickKey(catalog, other) !== ownKey);
  const duplicate =
    pick !== null &&
    valid &&
    others.some((other) => pickKey(catalog, other) === pickKey(catalog, pick));
  const nameTaken =
    label.trim() !== "" &&
    others.some(
      (other) =>
        pickText(catalog, other).toLowerCase() === label.trim().toLowerCase(),
    );
  const unavailable = !!chosen?.intraday_only && !intraday;
  const needsMinutes =
    !!chosen?.intraday_only && intraday && !MINUTE_FRAMES.includes(timeframe);

  /** The pick to save, or null after marking what stops it. */
  const submit = (): IndicatorPick | null => {
    setTried(true);
    if (
      !pick ||
      !valid ||
      duplicate ||
      nameTaken ||
      unavailable ||
      needsMinutes
    )
      return null;
    return pick;
  };
  /** A setting's problem, shown once it was touched or a save was tried. */
  const problemOf = (param: string) =>
    tried || values[param] !== undefined ? problems[param] : undefined;

  return {
    query,
    setQuery,
    groups,
    chosen,
    choose,
    label,
    setLabel,
    timeframe,
    setTimeframe,
    candles,
    setCandles,
    frames,
    values,
    setValue: (param: string, text: string) =>
      setValues((current) => ({ ...current, [param]: text })),
    problemOf,
    valid,
    pick,
    duplicate,
    nameTaken,
    unavailable,
    needsMinutes,
    submit,
  };
}

export type IndicatorFormState = ReturnType<typeof useIndicatorForm>;

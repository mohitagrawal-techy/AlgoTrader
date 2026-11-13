import type {
  CandleType,
  IndicatorPick,
  TechCatalog,
  TechOperand,
  TechRule,
  TechTimeframe,
} from "../../api";
import {
  CANDLE_NAMES,
  freshIndicator,
  operandPick,
  pickedOperand,
  pickKey,
  PRICE_NAMES,
} from "./picks";
import { PRESETS } from "./presets";

type Frame = { timeframe: TechTimeframe; candles?: CandleType };

/** How new rules start, and how a rule relates to the strategy's indicators. */
export function ruleHelpers(catalog: TechCatalog, picked: IndicatorPick[]) {
  // A new comparison starts at the close, from the Current candle when it is picked, against
  // the first other picked indicator on the same candles, or against a number.
  const candle = picked.find((pick) => pick.name === "candle");
  const others = picked.filter(
    (pick) =>
      !CANDLE_NAMES.includes(pick.name) &&
      (!candle ||
        ((pick.timeframe ?? "1d") === (candle.timeframe ?? "1d") &&
          (pick.candles ?? "normal") === (candle.candles ?? "normal"))),
  );
  const firstLeft = (): TechOperand =>
    candle
      ? { ...pickedOperand(catalog, candle), field: "close" }
      : freshIndicator(catalog, "close");
  const firstRight = (): TechOperand =>
    others.length
      ? pickedOperand(catalog, others[0])
      : { kind: "value", value: 0 };
  const firstFrame = (fallback: Frame): Frame => {
    const from = candle ?? others[0];
    return from
      ? { timeframe: from.timeframe ?? "1d", candles: from.candles ?? "normal" }
      : fallback;
  };
  const newCompare = (fallback: Frame): TechRule => ({
    type: "compare",
    ...firstFrame(fallback),
    left: firstLeft(),
    op: "above",
    right: firstRight(),
  });

  const keyOf = (operand: TechOperand, rule: Frame) =>
    pickKey(catalog, operandPick(operand, rule));
  /** The picked indicator a compare rule reads, which sets the candles it is read on. */
  const pickOf = (rule: TechRule): IndicatorPick | undefined => {
    if (rule.type !== "compare") return undefined;
    for (const operand of [rule.left, rule.right]) {
      if (
        operand.kind !== "indicator" ||
        PRICE_NAMES.includes(operand.name ?? "")
      )
        continue;
      const found = picked.find(
        (pick) => pickKey(catalog, pick) === keyOf(operand, rule),
      );
      if (found) return found;
    }
    return undefined;
  };
  /** Indicators a rule reads that are not among the strategy's on its candles. */
  const strays = (rule: TechRule): TechOperand[] =>
    rule.type !== "compare"
      ? []
      : [rule.left, rule.right].filter(
          (operand) =>
            operand.kind === "indicator" &&
            !PRICE_NAMES.includes(operand.name ?? "") &&
            !picked.some(
              (pick) => pickKey(catalog, pick) === keyOf(operand, rule),
            ),
        );
  // Only ready-made rules built from prices and the strategy's indicators are offered.
  const presets = PRESETS.filter((preset) => strays(preset.rule).length === 0);

  return { newCompare, pickOf, strays, presets };
}

export type RuleHelpers = ReturnType<typeof ruleHelpers>;

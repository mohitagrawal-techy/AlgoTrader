import { Trash2 } from "lucide-react";
import type {
  IndicatorPick,
  TechCatalog,
  TechOperand,
  TechRule,
} from "../../api";
import {
  normalPick,
  onRuleCandles,
  operandPick,
  pickKey,
  pickStandardText,
  pickText,
  readOn,
} from "../logic/picks";
import { patternOperand, type RuleHelpers } from "../logic/ruleDefaults";
import { Button, Select } from "../../components/ui";
import { OperandPicker } from "./OperandPicker";

/** The columns every condition row shares, and the headings above them. */
export const ROW_GRID =
  "grid items-start gap-2 md:grid-cols-[minmax(0,1fr)_10rem_minmax(0,1fr)_2.25rem]";

/** One condition of a signal, on one line: the value, the condition, what it is compared
 * with, and a button to remove it. Choosing a candle pattern picked under Indicators as the
 * value makes it a pattern condition: "Is" true or false. */
export function ConditionRow({
  id,
  index,
  rule,
  catalog,
  picked,
  helpers,
  problems,
  onChange,
  onRemove,
  onAddPick,
}: {
  id: string;
  index: number;
  rule: TechRule;
  catalog: TechCatalog;
  picked: IndicatorPick[];
  helpers: RuleHelpers;
  problems: Record<string, string>;
  onChange: (rule: TechRule) => void;
  onRemove: () => void;
  /** Adds an indicator to the strategy's Indicators, for one a condition reads but is not
   * there yet. */
  onAddPick?: (pick: IndicatorPick) => void;
}) {
  const strays = helpers.strays(rule);
  const frameOf = (chosen?: IndicatorPick) =>
    chosen
      ? {
          timeframe: chosen.timeframe ?? "1d",
          candles: chosen.candles ?? "normal",
        }
      : {};
  type Compare = Extract<TechRule, { type: "compare" }>;
  const ruleFrame = { timeframe: rule.timeframe, candles: rule.candles };
  // The Value's candles are the rule's: the condition is checked as each of them finishes.
  // Compared with may read other candles, such as a 15-minute opening range under a 5-minute
  // close; it keeps its own when the Value moves to other candles.
  const setLeft = (left: TechOperand, chosen?: IndicatorPick) => {
    if (rule.type !== "compare") return;
    const frame = chosen ? frameOf(chosen) : ruleFrame;
    const rightOn = {
      timeframe: rule.right.timeframe ?? rule.timeframe,
      candles: rule.right.candles ?? rule.candles,
    };
    onChange({
      ...rule,
      ...frame,
      left: onRuleCandles(left),
      right: readOn(rule.right, rightOn, frame as typeof ruleFrame),
    } as Compare);
  };
  const setRight = (right: TechOperand, chosen?: IndicatorPick) => {
    if (rule.type !== "compare") return;
    onChange({
      ...rule,
      right: chosen
        ? readOn(right, frameOf(chosen) as typeof ruleFrame, ruleFrame)
        : onRuleCandles(right),
    });
  };
  // A pattern picked as the value makes a pattern condition; anything else, a comparison.
  const toPattern = (chosen: IndicatorPick) =>
    onChange({
      type: "pattern",
      timeframe: chosen.timeframe ?? "1d",
      candles: chosen.candles ?? "normal",
      pattern: chosen.name,
      within: chosen.params.within ?? 1,
      present: rule.type === "pattern" ? rule.present : true,
      join: rule.join,
    });
  const toCompare = (left: TechOperand, chosen?: IndicatorPick) => {
    const fresh = helpers.newCompare({
      timeframe: rule.timeframe,
      candles: rule.candles,
    });
    onChange({
      ...fresh,
      ...frameOf(chosen),
      left,
      join: rule.join,
    } as TechRule);
  };

  return (
    <div
      id={`${id}`}
      role="group"
      aria-label={`Condition ${index + 1}`}
      className="flex flex-col gap-1.5"
    >
      <div className={ROW_GRID}>
        {rule.type === "compare" ? (
          <>
            <OperandPicker
              id={`${id}-left`}
              label="Value"
              catalog={catalog}
              operand={rule.left}
              allowNumber={false}
              problems={problems}
              picked={picked}
              frame={rule}
              onChange={setLeft}
              onPattern={toPattern}
            />
            <Select
              aria-label="Condition"
              value={rule.op}
              onChange={(event) =>
                onChange({
                  ...rule,
                  op: event.target.value as typeof rule.op,
                })
              }
            >
              {catalog.operators.map((op) => (
                <option key={op.value} value={op.value}>
                  {op.label}
                </option>
              ))}
            </Select>
            <OperandPicker
              id={`${id}-right`}
              label="Compared with"
              catalog={catalog}
              operand={rule.right}
              allowNumber
              problems={problems}
              picked={picked}
              frame={rule}
              onChange={setRight}
            />
          </>
        ) : (
          <>
            <OperandPicker
              id={`${id}-left`}
              label="Value"
              catalog={catalog}
              operand={patternOperand(rule)}
              allowNumber={false}
              problems={problems}
              picked={picked}
              frame={rule}
              onChange={toCompare}
              onPattern={toPattern}
            />
            <Select aria-label="Condition" value="is" onChange={() => {}}>
              <option value="is">Is</option>
            </Select>
            <Select
              aria-label="Compared with"
              value={rule.present ? "true" : "false"}
              onChange={(event) =>
                onChange({ ...rule, present: event.target.value === "true" })
              }
            >
              <option value="true">True</option>
              <option value="false">False</option>
            </Select>
          </>
        )}
        <Button
          variant="danger"
          size="icon"
          icon={Trash2}
          aria-label={`Remove condition ${index + 1}`}
          onClick={onRemove}
        />
      </div>
      {strays.map((operand) => {
        const wanted = normalPick(
          catalog,
          operand.name ?? "",
          operand.params,
          operandPick(operand, rule),
        );
        // The same indicator, with the same settings, picked on other candles.
        const elsewhere = picked.filter(
          (pick) =>
            pickKey(catalog, {
              ...pick,
              timeframe: rule.timeframe,
              candles: rule.candles ?? "normal",
            }) === pickKey(catalog, wanted),
        );
        return (
          <div
            key={pickKey(catalog, wanted)}
            className="flex flex-wrap items-center gap-2 text-[12.5px] text-accent-ink"
          >
            <span>
              {pickStandardText(catalog, wanted)} is not in Indicators.
            </span>
            {elsewhere.map((pick) => (
              <Button
                key={pickKey(catalog, pick)}
                size="sm"
                onClick={() =>
                  rule.type === "compare" && operand === rule.right
                    ? setRight(rule.right, pick)
                    : rule.type === "compare"
                      ? setLeft(rule.left, pick)
                      : onChange({ ...rule, ...frameOf(pick) } as TechRule)
                }
              >
                Use {pickText(catalog, pick)}
              </Button>
            ))}
            {onAddPick && (
              <Button size="sm" onClick={() => onAddPick(wanted)}>
                Add it to Indicators
              </Button>
            )}
          </div>
        );
      })}
    </div>
  );
}

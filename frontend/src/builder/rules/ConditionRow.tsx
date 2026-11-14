import { Trash2 } from "lucide-react";
import type {
  IndicatorPick,
  TechCatalog,
  TechOperand,
  TechRule,
} from "../../api";
import { operandPick, pickStandardText } from "../logic/picks";
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
}) {
  const strays = helpers.strays(rule);
  const frameOf = (chosen?: IndicatorPick) =>
    chosen
      ? {
          timeframe: chosen.timeframe ?? "1d",
          candles: chosen.candles ?? "normal",
        }
      : {};
  const withPick = (next: Partial<TechRule>, chosen?: IndicatorPick) =>
    onChange({ ...rule, ...next, ...frameOf(chosen) } as TechRule);
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
              onChange={(left, chosen) => withPick({ left }, chosen)}
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
              onChange={(right, chosen) => withPick({ right }, chosen)}
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
      {strays.length > 0 && (
        <p className="text-[12.5px] text-accent-ink">
          {strays
            .map((operand) =>
              pickStandardText(catalog, operandPick(operand, rule)),
            )
            .join(" and ")}{" "}
          {strays.length === 1 ? "is" : "are"} not among the strategy's
          indicators on these candles. Choose one from the list, or add it under
          Indicators.
        </p>
      )}
    </div>
  );
}

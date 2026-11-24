import type {
  CandleType,
  IndicatorPick,
  TechCatalog,
  TechOperand,
  TechTimeframe,
} from "../../api";
import { indicatorTitle } from "../../views/IndicatorTip";
import {
  byType,
  freshIndicator,
  indicatorOf,
  operandPick,
  patternOf,
  pickedOperand,
  pickKey,
  pickText,
  PRICE_NAMES,
} from "../logic/picks";
import { operandText } from "../logic/ruleText";
import { FieldMessage, NumberInput, Select } from "../../components/ui";

const OTHER = "__other";
const NUMBER = "__number";

/** One side of a condition, as one box: a price, one of the strategy's indicators (each
 * line its own choice), a picked candle pattern, or a number typed beside it. */
export function OperandPicker({
  id,
  label,
  catalog,
  operand,
  allowNumber,
  problems,
  picked,
  frame,
  onChange,
  onPattern,
  signals,
  signal,
  onSignal,
}: {
  id: string;
  /** What this side is, for screen readers; the column heading shows it. */
  label: string;
  catalog: TechCatalog;
  operand: TechOperand;
  allowNumber: boolean;
  problems: Record<string, string>;
  picked: IndicatorPick[];
  /** The rule's candles, which a picked indicator must be read on to match. */
  frame: { timeframe: TechTimeframe; candles?: CandleType };
  /** A picked indicator comes with its candles, which the rule then reads on. */
  onChange: (operand: TechOperand, pick?: IndicatorPick) => void;
  /** Offers the picked candle patterns too; choosing one makes the rule a pattern rule. */
  onPattern?: (pick: IndicatorPick) => void;
  /** The strategy's named signals, offered first; choosing one makes a signal condition. */
  signals?: string[];
  /** The signal this side reads now, if it is a signal condition. */
  signal?: string;
  onSignal?: (name: string) => void;
}) {
  const indicator =
    operand.kind === "indicator"
      ? indicatorOf(catalog, operand.name)
      : undefined;
  const message = Object.entries(problems).find(
    ([key]) => key === id || key.startsWith(`${id}-`),
  )?.[1];
  const isPrice = PRICE_NAMES.includes(operand.name ?? "");
  // Patterns are offered only where the rule can become a pattern rule.
  const listed = picked
    .map((pick, index) => ({ pick, index }))
    .filter(({ pick }) => onPattern || !patternOf(catalog, pick.name));
  const pickIndex =
    operand.kind === "indicator" && !isPrice
      ? picked.findIndex(
          (pick) =>
            pickKey(catalog, pick) ===
            pickKey(catalog, operandPick(operand, frame)),
        )
      : -1;
  // An indicator with several lines (a candle's open, high, low and close) lists each line as
  // its own choice: "pick:<index>:<line>".
  const pickChoice = (index: number, field?: string | null) =>
    field ? `pick:${index}:${field}` : `pick:${index}`;
  const choice =
    signal !== undefined
      ? `signal:${signal}`
      : operand.kind === "value"
        ? NUMBER
        : isPrice
          ? (operand.name ?? "")
          : pickIndex >= 0
            ? pickChoice(
                pickIndex,
                indicator?.fields.length ? operand.field : null,
              )
            : OTHER;

  const choose = (value: string) => {
    if (value === OTHER) return;
    if (value.startsWith("signal:")) {
      onSignal?.(value.slice("signal:".length));
      return;
    }
    if (value === NUMBER) onChange({ kind: "value", value: 0 });
    else if (value.startsWith("pick:")) {
      const [, index, field] = value.split(":");
      const pick = picked[Number(index)];
      if (patternOf(catalog, pick.name)) {
        onPattern?.(pick);
        return;
      }
      const chosen = pickedOperand(catalog, pick);
      onChange(field ? { ...chosen, field } : chosen, pick);
    } else onChange(freshIndicator(catalog, value));
  };
  const ago =
    signal === undefined && operand.kind === "indicator"
      ? (operand.bars_ago ?? 0)
      : 0;

  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex gap-2">
        <Select
          id={`${id}-name`}
          className="min-w-0 flex-1"
          aria-label={label}
          aria-invalid={!!message || undefined}
          // Long choices are cut off in the box; the tooltip shows the whole of it.
          title={
            operand.kind === "indicator"
              ? operandText(catalog, { ...operand, bars_ago: 0 })
              : undefined
          }
          value={choice}
          onChange={(e) => choose(e.target.value)}
        >
          {allowNumber && <option value={NUMBER}>A number</option>}
          {onSignal && (signals?.length ?? 0) > 0 && (
            <optgroup label="Signals">
              {signals?.map((name) => (
                <option key={name} value={`signal:${name}`}>
                  {name}
                </option>
              ))}
            </optgroup>
          )}
          {signal !== undefined && !signals?.includes(signal) && (
            <option value={`signal:${signal}`}>
              {signal} (no such signal)
            </option>
          )}
          <optgroup label="Price">
            {catalog.indicators
              .filter((item) => PRICE_NAMES.includes(item.name))
              .map((item) => (
                <option
                  key={item.name}
                  value={item.name}
                  title={indicatorTitle(item)}
                >
                  {item.label}
                </option>
              ))}
          </optgroup>
          {listed.length ? (
            byType(catalog, listed, (entry) => entry.pick.name).map((group) => (
              <optgroup key={group.title} label={group.title}>
                {group.items.flatMap(({ pick, index }) => {
                  const found = indicatorOf(catalog, pick.name);
                  const lines = found?.fields.length
                    ? found.fields
                    : [{ value: "", label: "" }];
                  return lines.map((line) => (
                    <option
                      key={`${index}-${line.value}`}
                      value={pickChoice(index, line.value || null)}
                      title={indicatorTitle(found)}
                    >
                      {pickText(catalog, pick)}
                      {line.label ? `: ${line.label}` : ""}
                    </option>
                  ));
                })}
              </optgroup>
            ))
          ) : (
            <optgroup label="No indicators picked yet" />
          )}
          {choice === OTHER && (
            <option value={OTHER}>
              {operandText(catalog, { ...operand, bars_ago: 0 })} (not in
              Indicators)
            </option>
          )}
        </Select>
        {signal === undefined && operand.kind === "value" && (
          <NumberInput
            id={id}
            className="w-28 shrink-0"
            label={`${label}: number`}
            step={0.5}
            value={operand.value ?? null}
            invalid={!!problems[id]}
            onChange={(value) => onChange({ ...operand, value })}
          />
        )}
      </div>
      {ago > 0 && (
        <p className="text-[12px] text-ink-3">
          {ago} candle{ago === 1 ? "" : "s"} ago.{" "}
          <button
            type="button"
            className="cursor-pointer text-accent-ink underline"
            onClick={() => onChange({ ...operand, bars_ago: 0 })}
          >
            Use the latest candle
          </button>
        </p>
      )}
      <FieldMessage id={`${id}-name`} problem={message} />
    </div>
  );
}

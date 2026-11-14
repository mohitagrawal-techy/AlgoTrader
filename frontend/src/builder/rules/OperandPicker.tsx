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
  pickedOperand,
  pickKey,
  pickText,
  PRICE_NAMES,
} from "../logic/picks";
import { operandText } from "../logic/ruleText";
import { FieldMessage, Hint, NumberInput, Select } from "../../components/ui";

const OTHER = "__other";
const NUMBER = "__number";

/** One side of a comparison: a price, one of the strategy's indicators, or a number, with
 * the line it reads and how many candles back. */
export function OperandPicker({
  id,
  catalog,
  operand,
  allowNumber,
  problems,
  picked,
  frame,
  onChange,
}: {
  id: string;
  catalog: TechCatalog;
  operand: TechOperand;
  allowNumber: boolean;
  problems: Record<string, string>;
  picked: IndicatorPick[];
  /** The rule's candles, which a picked indicator must be read on to match. */
  frame: { timeframe: TechTimeframe; candles?: CandleType };
  /** A picked indicator comes with its candles, which the rule then reads on. */
  onChange: (operand: TechOperand, pick?: IndicatorPick) => void;
}) {
  const indicator =
    operand.kind === "indicator"
      ? indicatorOf(catalog, operand.name)
      : undefined;
  const message = Object.entries(problems).find(
    ([key]) => key === id || key.startsWith(`${id}-`),
  )?.[1];
  const isPrice = PRICE_NAMES.includes(operand.name ?? "");
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
    operand.kind === "value"
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
    if (value === NUMBER) onChange({ kind: "value", value: 0 });
    else if (value.startsWith("pick:")) {
      const [, index, field] = value.split(":");
      const pick = picked[Number(index)];
      const chosen = pickedOperand(catalog, pick);
      onChange(field ? { ...chosen, field } : chosen, pick);
    } else onChange(freshIndicator(catalog, value));
  };

  return (
    <div className="flex min-w-0 flex-1 basis-56 flex-col gap-1.5">
      <Select
        id={`${id}-name`}
        small
        aria-label="Value to compare"
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
        {picked.length ? (
          byType(
            catalog,
            picked.map((pick, index) => ({ pick, index })),
            (entry) => entry.pick.name,
          ).map((group) => (
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
      <div className="flex flex-wrap gap-2">
        {operand.kind === "value" ? (
          <Small label="Number" id={id}>
            <NumberInput
              id={id}
              small
              step={0.5}
              value={operand.value ?? null}
              invalid={!!problems[id]}
              onChange={(value) => onChange({ ...operand, value })}
            />
          </Small>
        ) : (
          <>
            {/* Picked indicators list each line above; this is for one not in Indicators. */}
            {choice === OTHER && indicator && indicator.fields.length > 0 && (
              <Small label="Line" id={`${id}-field`}>
                <Select
                  id={`${id}-field`}
                  small
                  className="w-auto max-w-64"
                  value={operand.field ?? ""}
                  onChange={(event) =>
                    onChange({ ...operand, field: event.target.value })
                  }
                >
                  {indicator.fields.map((field) => (
                    <option key={field.value} value={field.value}>
                      {field.label}
                    </option>
                  ))}
                </Select>
              </Small>
            )}
            <Small label="Candles ago" id={`${id}-ago`}>
              <NumberInput
                id={`${id}-ago`}
                small
                value={operand.bars_ago ?? 0}
                invalid={!!problems[`${id}-ago`]}
                onChange={(value) =>
                  onChange({ ...operand, bars_ago: value ?? 0 })
                }
              />
            </Small>
          </>
        )}
      </div>
      {!isPrice &&
        operand.kind === "indicator" &&
        indicator &&
        indicator.params.length > 0 && (
          <Hint>Settings come from the Indicators section.</Hint>
        )}
      <FieldMessage id={`${id}-name`} problem={message} />
    </div>
  );
}

function Small({
  label,
  id,
  children,
}: {
  label: string;
  id: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-w-28 flex-col gap-1">
      <label htmlFor={id} className="text-[11.5px] font-semibold text-ink-3">
        {label}
      </label>
      {children}
    </div>
  );
}

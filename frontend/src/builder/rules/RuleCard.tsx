import { Trash2 } from "lucide-react";
import type {
  CandleType,
  IndicatorPick,
  TechCatalog,
  TechRule,
  TechTimeframe,
} from "../../api";
import { operandPick, pickStandardText, pickText } from "../logic/picks";
import type { RuleHelpers } from "../logic/ruleDefaults";
import { ruleText } from "../logic/ruleText";
import { Button } from "../ui/Button";
import { Select } from "../ui/inputs";
import { OperandPicker } from "./OperandPicker";
import { PatternFields } from "./PatternFields";

/** One rule: what kind it is, the candles it reads, its comparison or pattern, and the rule
 * as a sentence. */
export function RuleCard({
  id,
  index,
  rule,
  catalog,
  picked,
  helpers,
  frames,
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
  frames: TechCatalog["timeframes"];
  problems: Record<string, string>;
  onChange: (rule: TechRule) => void;
  onRemove: () => void;
}) {
  const pick = helpers.pickOf(rule);
  const strays = helpers.strays(rule);
  const candleTypes = catalog.candle_types ?? [];
  const frameLabel =
    catalog.timeframes
      .find((t) => t.value === rule.timeframe)
      ?.label.toLowerCase() ?? rule.timeframe;
  const setType = (type: TechRule["type"]) =>
    onChange(
      type === "pattern"
        ? {
            type,
            timeframe: rule.timeframe,
            candles: rule.candles,
            pattern: catalog.patterns[0].name,
            within: 1,
            present: true,
          }
        : helpers.newCompare({
            timeframe: rule.timeframe,
            candles: rule.candles,
          }),
    );
  const withPick = (next: Partial<TechRule>, chosen?: IndicatorPick) =>
    onChange({
      ...rule,
      ...next,
      ...(chosen
        ? {
            timeframe: chosen.timeframe ?? "1d",
            candles: chosen.candles ?? "normal",
          }
        : {}),
    } as TechRule);

  return (
    <li
      className="rounded-md border border-line bg-surface-2/60 p-3"
      aria-label={`Rule ${index + 1}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Select
          small
          className="w-auto"
          aria-label="Kind of rule"
          value={rule.type}
          onChange={(event) => setType(event.target.value as TechRule["type"])}
        >
          <option value="compare">Indicator or price</option>
          <option value="pattern">Candle pattern</option>
        </Select>
        {pick ? (
          <span className="text-[12.5px] text-ink-3">
            On {frameLabel}
            {rule.candles === "heikin_ashi" ? " Heikin-Ashi" : ""} candles, set
            by{" "}
            <strong className="font-semibold text-ink">
              {pickText(catalog, pick)}
            </strong>
          </span>
        ) : (
          <>
            <Select
              small
              className="w-auto"
              aria-label="Timeframe"
              value={rule.timeframe}
              onChange={(event) =>
                onChange({
                  ...rule,
                  timeframe: event.target.value as TechTimeframe,
                })
              }
            >
              {frames.map((frame) => (
                <option key={frame.value} value={frame.value}>
                  {frame.label} candles
                </option>
              ))}
            </Select>
            {candleTypes.length > 0 && (
              <Select
                small
                className="w-auto"
                aria-label="Candle type"
                value={rule.candles ?? "normal"}
                onChange={(event) =>
                  onChange({
                    ...rule,
                    candles: event.target.value as CandleType,
                  })
                }
              >
                {candleTypes.map((kind) => (
                  <option key={kind.value} value={kind.value}>
                    {kind.label}
                  </option>
                ))}
              </Select>
            )}
          </>
        )}
        <Button
          variant="ghost"
          size="sm"
          icon={Trash2}
          className="ml-auto text-down hover:text-down"
          aria-label={`Remove rule ${index + 1}`}
          onClick={onRemove}
        >
          Remove
        </Button>
      </div>

      {rule.type === "compare" ? (
        <div className="mt-3 flex flex-wrap items-start gap-2">
          <OperandPicker
            id={`${id}-left`}
            catalog={catalog}
            operand={rule.left}
            allowNumber={false}
            problems={problems}
            picked={picked}
            frame={rule}
            onChange={(left, chosen) => withPick({ left }, chosen)}
          />
          <Select
            small
            className="w-auto basis-32"
            aria-label="Comparison"
            value={rule.op}
            onChange={(event) =>
              onChange({ ...rule, op: event.target.value as typeof rule.op })
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
            catalog={catalog}
            operand={rule.right}
            allowNumber
            problems={problems}
            picked={picked}
            frame={rule}
            onChange={(right, chosen) => withPick({ right }, chosen)}
          />
          {strays.length > 0 && (
            <p className="w-full text-[12.5px] text-accent-ink">
              {strays
                .map((operand) =>
                  pickStandardText(catalog, operandPick(operand, rule)),
                )
                .join(" and ")}{" "}
              {strays.length === 1 ? "is" : "are"} not among the strategy's
              indicators on these candles. Choose one from the list, or add it
              under Indicators.
            </p>
          )}
        </div>
      ) : (
        <PatternFields
          id={id}
          rule={rule}
          catalog={catalog}
          problems={problems}
          onChange={onChange}
        />
      )}
      <p className="mt-3 text-[12.5px] text-ink-3">
        Reads as: <span className="text-ink">{ruleText(catalog, rule)}</span>
      </p>
    </li>
  );
}

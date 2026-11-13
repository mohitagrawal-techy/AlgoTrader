import type { TechCatalog, TechRule } from "../../api";
import { Field, Hint, NumberInput, Select } from "../../components/ui";

type PatternRule = Extract<TechRule, { type: "pattern" }>;

const BIASES = ["bullish", "bearish", "neutral"] as const;

/** A candle-pattern rule: seen or not, which pattern, and within how many candles. */
export function PatternFields({
  id,
  rule,
  catalog,
  problems,
  onChange,
}: {
  id: string;
  rule: PatternRule;
  catalog: TechCatalog;
  problems: Record<string, string>;
  onChange: (rule: TechRule) => void;
}) {
  const description = catalog.patterns.find(
    (p) => p.name === rule.pattern,
  )?.description;
  return (
    <div className="mt-3 flex flex-wrap items-end gap-2">
      <Select
        small
        className="w-auto"
        aria-label="Seen or not seen"
        value={rule.present ? "yes" : "no"}
        onChange={(event) =>
          onChange({ ...rule, present: event.target.value === "yes" })
        }
      >
        <option value="yes">Seen</option>
        <option value="no">Not seen</option>
      </Select>
      <Select
        small
        className="w-auto"
        aria-label="Candle pattern"
        value={rule.pattern}
        onChange={(event) => onChange({ ...rule, pattern: event.target.value })}
      >
        {BIASES.map((bias) => (
          <optgroup
            key={bias}
            label={`${bias[0].toUpperCase()}${bias.slice(1)}`}
          >
            {catalog.patterns
              .filter((p) => p.bias === bias)
              .map((p) => (
                <option key={p.name} value={p.name}>
                  {p.label}
                </option>
              ))}
          </optgroup>
        ))}
      </Select>
      <Field
        id={`${id}-within`}
        label="Within the last N candles"
        problem={problems[`${id}-within`]}
        className="w-44"
      >
        <NumberInput
          id={`${id}-within`}
          small
          value={rule.within}
          invalid={!!problems[`${id}-within`]}
          onChange={(value) => onChange({ ...rule, within: value ?? 1 })}
        />
      </Field>
      {description && <Hint className="w-full">{description}</Hint>}
    </div>
  );
}

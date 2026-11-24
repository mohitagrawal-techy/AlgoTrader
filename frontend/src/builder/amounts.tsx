import type { ExitUnit, LegUnit } from "../api";
import { LEG_UNIT_DEFAULTS, UNIT_DEFAULTS, UNITS } from "./logic/spec";
import { InputGroup, NumberInput, Select } from "../components/ui";

/** An amount with its unit beside it: × premium, ₹ per lot or points. Changing the unit
 * starts the amount at a sensible value for that unit. */
export function AmountInput({
  id,
  value,
  unit,
  invalid,
  optional,
  onChange,
}: {
  id: string;
  value: number | null | undefined;
  unit: ExitUnit | undefined;
  invalid: boolean;
  optional?: boolean;
  onChange: (value: number | null, unit: ExitUnit) => void;
}) {
  const current = unit ?? "premium";
  return (
    <InputGroup>
      <NumberInput
        id={id}
        value={value ?? null}
        step={current === "premium" ? 0.05 : current === "points" ? 1 : 100}
        placeholder={optional ? "Off" : undefined}
        invalid={invalid}
        onChange={(next) => onChange(next, current)}
      />
      <Select
        aria-label="Unit"
        className="w-auto shrink-0"
        value={current}
        onChange={(event) => {
          const next = event.target.value as ExitUnit;
          onChange(
            value === null || value === undefined ? null : UNIT_DEFAULTS[next],
            next,
          );
        }}
      >
        {UNITS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </Select>
    </InputGroup>
  );
}

/** A leg's own stop or target: an amount, empty for off, and % or points beside it. */
export function LegLevel({
  id,
  label,
  value,
  unit,
  invalid,
  onChange,
}: {
  id: string;
  label: string;
  value: number | null | undefined;
  unit: LegUnit | undefined;
  invalid: boolean;
  onChange: (value: number | null, unit: LegUnit) => void;
}) {
  const current = unit ?? "pct";
  return (
    <InputGroup>
      <NumberInput
        id={id}
        small
        value={value ?? null}
        step={current === "pct" ? 5 : 1}
        placeholder="Off"
        invalid={invalid}
        onChange={(next) => onChange(next, current)}
      />
      <Select
        aria-label={`${label} unit`}
        small
        className="w-auto shrink-0"
        value={current}
        onChange={(event) => {
          const next = event.target.value as LegUnit;
          onChange(
            value === null || value === undefined
              ? null
              : LEG_UNIT_DEFAULTS[next],
            next,
          );
        }}
      >
        <option value="pct">%</option>
        <option value="points">pts</option>
      </Select>
    </InputGroup>
  );
}

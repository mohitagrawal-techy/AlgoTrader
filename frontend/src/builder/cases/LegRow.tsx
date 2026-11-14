import { X } from "lucide-react";
import type { ReactNode } from "react";
import type { LabLeg } from "../../api";
import { LegLevel } from "../amounts";
import {
  Button,
  cx,
  InputGroup,
  NumberInput,
  Select,
} from "../../components/ui";

const STEP = { delta: 0.05, premium: 5, atm_offset: 1 };
const START = { delta: 0.3, premium: 100, atm_offset: 0 };
const VALUE_NAME = {
  delta: "Delta",
  premium: "Premium in rupees",
  atm_offset: "Strikes from ATM",
};

/** The legs table's columns on a wide panel; narrower, each leg is a card of labelled cells. */
export const LEG_COLUMNS =
  "@4xl:grid-cols-[5.5rem_4.5rem_minmax(0,1fr)_minmax(0,1.7fr)_4.5rem_minmax(0,1fr)_minmax(0,1fr)_2rem]";
export const LEG_HEADINGS = [
  "Action",
  "Option",
  "Expiry",
  "Strike by",
  "Lots",
  "Stop loss",
  "Take profit",
];

function Cell({
  id,
  label,
  className,
  children,
}: {
  id: string;
  label: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cx("flex min-w-0 flex-col gap-1", className)}>
      <label
        htmlFor={id}
        className="text-[11.5px] font-semibold text-ink-3 @4xl:sr-only"
      >
        {label}
      </label>
      {children}
    </div>
  );
}

/** One option in the trade: buy or sell, call or put, expiry, strike, lots, and its own
 * stop loss and take profit. */
export function LegRow({
  id,
  index,
  leg,
  problems,
  removable,
  onChange,
  onRemove,
}: {
  id: string;
  index: number;
  leg: LabLeg;
  problems: Record<string, string>;
  removable: boolean;
  onChange: (change: Partial<LabLeg>) => void;
  onRemove: () => void;
}) {
  const hasProblem = ["ratio", "value", "stop", "target"].some(
    (field) => problems[`${id}-${field}`],
  );
  const buy = leg.action === "buy";
  return (
    <li
      aria-label={`Leg ${index + 1}`}
      className={cx(
        "relative grid grid-cols-2 items-center gap-x-3 gap-y-2.5 rounded-md border bg-surface p-3 pt-4 @xl:grid-cols-4 @4xl:py-2",
        LEG_COLUMNS,
        hasProblem ? "border-down/60" : "border-line",
      )}
    >
      <span className="absolute -top-2.5 left-3 rounded-sm bg-surface px-1.5 text-[11.5px] font-semibold text-ink-2 @4xl:-top-2 @4xl:text-[11px]">
        Leg {index + 1}
      </span>
      <Cell id={`${id}-action`} label="Action">
        <Select
          id={`${id}-action`}
          small
          value={leg.action}
          className={cx("font-semibold", buy ? "text-call" : "text-put")}
          onChange={(event) =>
            onChange({ action: event.target.value as LabLeg["action"] })
          }
        >
          <option value="buy">Buy</option>
          <option value="sell">Sell</option>
        </Select>
      </Cell>
      <Cell id={`${id}-type`} label="Option">
        <Select
          id={`${id}-type`}
          small
          value={leg.option_type}
          onChange={(event) =>
            onChange({
              option_type: event.target.value as LabLeg["option_type"],
            })
          }
        >
          <option value="CE">CE</option>
          <option value="PE">PE</option>
        </Select>
      </Cell>
      <Cell id={`${id}-expiry`} label="Expiry">
        <Select
          id={`${id}-expiry`}
          small
          value={leg.expiry}
          onChange={(event) => onChange({ expiry: Number(event.target.value) })}
        >
          <option value={0}>Entry expiry</option>
          <option value={1}>Next expiry</option>
          <option value={2}>Expiry after next</option>
        </Select>
      </Cell>
      <Cell
        id={`${id}-select`}
        label="Strike by"
        className="col-span-2 @4xl:col-span-1"
      >
        <InputGroup>
          <Select
            id={`${id}-select`}
            small
            value={leg.select}
            className="min-w-0 flex-1"
            onChange={(event) => {
              const select = event.target.value as LabLeg["select"];
              onChange({ select, value: START[select] });
            }}
          >
            <option value="atm_offset">ATM ± strikes</option>
            <option value="delta">Delta</option>
            <option value="premium">Premium ₹</option>
          </Select>
          <NumberInput
            id={`${id}-value`}
            small
            label={VALUE_NAME[leg.select]}
            value={leg.value}
            step={STEP[leg.select]}
            invalid={!!problems[`${id}-value`]}
            className="w-24 shrink-0"
            onChange={(value) => onChange({ value: value as number })}
          />
        </InputGroup>
      </Cell>
      <Cell id={`${id}-ratio`} label="Lots">
        <NumberInput
          id={`${id}-ratio`}
          small
          value={leg.ratio}
          invalid={!!problems[`${id}-ratio`]}
          onChange={(value) => onChange({ ratio: value as number })}
        />
      </Cell>
      <Cell id={`${id}-stop`} label="Stop loss">
        <LegLevel
          id={`${id}-stop`}
          label={`Leg ${index + 1} stop loss`}
          value={leg.stop}
          unit={leg.stop_unit}
          invalid={!!problems[`${id}-stop`]}
          onChange={(stop, stop_unit) => onChange({ stop, stop_unit })}
        />
      </Cell>
      <Cell id={`${id}-target`} label="Take profit">
        <LegLevel
          id={`${id}-target`}
          label={`Leg ${index + 1} take profit`}
          value={leg.target}
          unit={leg.target_unit}
          invalid={!!problems[`${id}-target`]}
          onChange={(target, target_unit) => onChange({ target, target_unit })}
        />
      </Cell>
      {removable ? (
        <Button
          variant="ghost"
          size="icon"
          icon={X}
          aria-label={`Remove leg ${index + 1}`}
          onClick={onRemove}
          className="absolute -top-3 right-2 size-6 rounded-sm border-line bg-surface text-ink-3 hover:text-down @4xl:static @4xl:size-8 @4xl:border-0"
        />
      ) : (
        <span className="hidden @4xl:block" aria-hidden />
      )}
    </li>
  );
}

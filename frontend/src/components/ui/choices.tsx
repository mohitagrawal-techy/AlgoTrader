import { useId, type ReactNode } from "react";
import { cx } from "./Button";

const PILL = cx(
  "inline-flex h-8 cursor-pointer select-none items-center justify-center whitespace-nowrap px-3 text-[13px] font-semibold",
  "text-ink-2 hover:bg-hover hover:text-ink peer-checked:bg-accent-wash peer-checked:text-ink",
  "peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent",
);

/** One choice from a few, as joined buttons. Real radio inputs underneath, so the arrow
 * keys move between them and a screen reader announces the group. An option's `count`
 * shows how many items that choice would list. */
export function Segmented<T extends string>({
  name,
  label,
  value,
  options,
  onChange,
  className,
}: {
  name?: string;
  label: string;
  value: T;
  options: readonly { value: T; label: string; count?: number }[];
  onChange: (value: T) => void;
  className?: string;
}) {
  const ownName = useId();
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cx(
        "inline-flex max-w-full overflow-x-auto rounded-md border border-line bg-surface",
        className,
      )}
    >
      {options.map((option) => (
        <label
          key={option.value}
          className="flex flex-1 border-line [&+&]:border-l"
        >
          <input
            type="radio"
            className="peer sr-only"
            name={name ?? ownName}
            checked={value === option.value}
            onChange={() => onChange(option.value)}
          />
          <span className={cx(PILL, "group w-full gap-1.5")}>
            {option.label}
            {option.count !== undefined && (
              <span className="text-[11.5px] font-medium tabular-nums opacity-70">
                {option.count}
              </span>
            )}
          </span>
        </label>
      ))}
    </div>
  );
}

/** A choice that is on or off, shown as a pill, such as a symbol to trade on. */
export function ChipCheck({
  checked,
  onChange,
  children,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  children: ReactNode;
}) {
  return (
    <label className="inline-flex">
      <input
        type="checkbox"
        className="peer sr-only"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span
        className={cx(
          PILL,
          "rounded-sm border border-line peer-checked:border-accent",
        )}
      >
        {children}
      </span>
    </label>
  );
}

/** An on/off switch with its label beside it. */
export function Switch({
  id,
  checked,
  disabled,
  describedBy,
  onChange,
  children,
}: {
  id: string;
  checked: boolean;
  disabled?: boolean;
  describedBy?: string;
  onChange: (checked: boolean) => void;
  children: ReactNode;
}) {
  return (
    <label
      htmlFor={id}
      className={cx(
        "inline-flex items-center gap-2.5",
        disabled ? "cursor-not-allowed" : "cursor-pointer",
      )}
    >
      <input
        id={id}
        type="checkbox"
        role="switch"
        className="peer sr-only"
        checked={checked}
        disabled={disabled}
        aria-describedby={describedBy}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span
        aria-hidden
        className={cx(
          "relative h-5 w-9 shrink-0 rounded-full border border-axis bg-surface-2 transition-colors",
          "after:absolute after:top-0.5 after:left-0.5 after:size-3.5 after:rounded-full after:bg-ink-3",
          "after:transition-transform peer-checked:border-accent peer-checked:bg-accent",
          "peer-checked:after:translate-x-4 peer-checked:after:bg-white",
          "peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent",
          "peer-disabled:opacity-50",
        )}
      />
      <span className="text-[13.5px] font-semibold text-ink">{children}</span>
    </label>
  );
}

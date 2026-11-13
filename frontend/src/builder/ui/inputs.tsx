import {
  useEffect,
  useState,
  type InputHTMLAttributes,
  type SelectHTMLAttributes,
} from "react";
import { cx } from "./Button";

/** The look every text box and list shares, with a red edge when it needs fixing. */
export const CONTROL = cx(
  "h-9 w-full min-w-0 rounded-md border border-line bg-surface px-2.5 text-[13.5px] text-ink",
  "placeholder:text-ink-3 hover:border-axis focus:border-accent focus:outline-none focus:ring-3",
  "focus:ring-accent-wash disabled:opacity-60 aria-invalid:border-down",
);
const SMALL = "h-8 text-[13px]";

/** Controls inside a Field point at its message, so a screen reader reads the problem. */
const described = (id: string) => `${id}-msg`;

export function TextInput({
  id,
  invalid,
  small,
  className,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & {
  id: string;
  invalid?: boolean;
  small?: boolean;
}) {
  return (
    <input
      id={id}
      aria-invalid={invalid || undefined}
      aria-describedby={described(id)}
      className={cx(CONTROL, small && SMALL, className)}
      {...rest}
    />
  );
}

/** A number box that keeps half-typed text such as "0." until the value changes elsewhere.
 * Empty means null. */
export function NumberInput({
  id,
  value,
  onChange,
  invalid,
  small,
  step = 1,
  placeholder,
  label,
  className,
}: {
  id: string;
  value: number | null;
  onChange: (value: number | null) => void;
  invalid?: boolean;
  small?: boolean;
  step?: number;
  placeholder?: string;
  /** A name for screen readers when no visible label points at it. */
  label?: string;
  className?: string;
}) {
  const [text, setText] = useState(value === null ? "" : String(value));
  useEffect(() => {
    const typed = text === "" ? null : Number(text);
    if (typed !== value) setText(value === null ? "" : String(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <input
      id={id}
      type="number"
      inputMode="decimal"
      aria-label={label}
      aria-invalid={invalid || undefined}
      aria-describedby={described(id)}
      className={cx(CONTROL, small && SMALL, "tabular-nums", className)}
      value={text}
      step={step}
      placeholder={placeholder}
      onChange={(event) => {
        setText(event.target.value);
        onChange(event.target.value === "" ? null : Number(event.target.value));
      }}
    />
  );
}

export function Select({
  id,
  invalid,
  small,
  className,
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement> & {
  invalid?: boolean;
  small?: boolean;
}) {
  return (
    <select
      id={id}
      aria-invalid={invalid || undefined}
      aria-describedby={id ? described(id) : undefined}
      className={cx(CONTROL, small && SMALL, "cursor-pointer pr-7", className)}
      {...rest}
    >
      {children}
    </select>
  );
}

/** Controls side by side as one, such as an amount and its unit. */
export function InputGroup({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cx(
        "flex min-w-0 [&>*]:rounded-none [&>*:first-child]:rounded-l-md [&>*:last-child]:rounded-r-md",
        "[&>*+*]:-ml-px [&>*:focus]:z-10",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** A fixed word inside an input group, such as "days". */
export function Addon({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex h-9 items-center border border-line bg-surface-2 px-2.5 text-[13px] text-ink-2">
      {children}
    </span>
  );
}

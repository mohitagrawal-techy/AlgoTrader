import { CircleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "./Button";

/** A label, its control, and below it either the problem to fix or a hint. The message's id
 * is the one the controls point at, so screen readers read it with the control. Without an
 * `id` the label wraps the control instead, for a simple box with no message to link. */
export function Field({
  id,
  label,
  hint,
  problem,
  className,
  children,
}: {
  id?: string;
  label: ReactNode;
  hint?: ReactNode;
  problem?: string;
  className?: string;
  children: ReactNode;
}) {
  if (!id) {
    return (
      <label className={cx("flex min-w-0 flex-col gap-1.5", className)}>
        <span className="text-[12.5px] font-semibold text-ink-2">{label}</span>
        {children}
        {hint && <Hint>{hint}</Hint>}
      </label>
    );
  }
  return (
    <div className={cx("flex min-w-0 flex-col gap-1.5", className)}>
      <label htmlFor={id} className="text-[12.5px] font-semibold text-ink-2">
        {label}
      </label>
      {children}
      <FieldMessage id={id} problem={problem} hint={hint} />
    </div>
  );
}

export function FieldMessage({
  id,
  problem,
  hint,
}: {
  id: string;
  problem?: string;
  hint?: ReactNode;
}) {
  if (problem) {
    return (
      <p
        id={`${id}-msg`}
        className="flex items-start gap-1 text-[12.5px] text-down"
      >
        <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" />
        {problem}
      </p>
    );
  }
  if (!hint) return null;
  return (
    <p id={`${id}-msg`} className="text-[12.5px] leading-snug text-ink-3">
      {hint}
    </p>
  );
}

/** A label for a group of controls that has no single control to point at. */
export function GroupLabel({
  id,
  children,
}: {
  id: string;
  children: ReactNode;
}) {
  return (
    <span id={id} className="text-[12.5px] font-semibold text-ink-2">
      {children}
    </span>
  );
}

/** Small explanatory text under a block. */
export function Hint({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <p className={cx("text-[12.5px] leading-snug text-ink-3", className)}>
      {children}
    </p>
  );
}

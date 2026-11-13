import type { ReactNode, Ref } from "react";
import { cx } from "./Button";

/** A data table that scrolls sideways inside its own box on narrow screens, so the page
 * itself never does. `compact` tightens the rows for dense lists. */
export function Table({
  compact,
  className,
  wrapClassName,
  wrapRef,
  label,
  children,
}: {
  compact?: boolean;
  className?: string;
  wrapClassName?: string;
  wrapRef?: Ref<HTMLDivElement>;
  /** A name for screen readers when the table has no caption. */
  label?: string;
  children: ReactNode;
}) {
  return (
    <div className={cx("table-wrap", wrapClassName)} ref={wrapRef}>
      <table
        className={cx("table", compact && "compact-table", className)}
        aria-label={label}
      >
        {children}
      </table>
    </div>
  );
}

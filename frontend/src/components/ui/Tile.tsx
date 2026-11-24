import type { ReactNode } from "react";
import { cx } from "./Button";

/** One figure with its label, and an optional line under it. */
export function Tile({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {hint && <div className="tile-hint">{hint}</div>}
    </div>
  );
}

/** A row of tiles. `columns` fixes how many sit side by side on a wide screen; without it
 * the row's own class decides. */
export function Tiles({
  columns,
  className,
  children,
}: {
  columns?: 3 | 4 | 6;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cx("tiles", columns && `tiles-${columns}`, className)}>
      {children}
    </div>
  );
}

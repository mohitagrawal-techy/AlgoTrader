import type { ButtonHTMLAttributes } from "react";
import { cx } from "./Button";

/** A small toggle or action in a chart's toolbar. `pressed` marks a toggle that is on. */
export function ChartChip({
  pressed,
  className,
  type = "button",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { pressed?: boolean }) {
  return (
    <button
      type={type}
      aria-pressed={pressed}
      className={cx("chart-chip", className)}
      {...rest}
    />
  );
}

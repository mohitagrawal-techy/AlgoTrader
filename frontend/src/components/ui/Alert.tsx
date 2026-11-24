import {
  CircleAlert,
  Info,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "./Button";

const TONES: Record<
  "info" | "warning" | "critical",
  { icon: LucideIcon; box: string; mark: string }
> = {
  info: {
    icon: Info,
    box: "border-l-accent bg-surface",
    mark: "text-accent-ink",
  },
  warning: {
    icon: TriangleAlert,
    box: "border-l-warning bg-surface",
    mark: "text-warning",
  },
  critical: {
    icon: CircleAlert,
    box: "border-l-down bg-critical-wash",
    mark: "text-down",
  },
};

/** A message in the page: its colour always comes with an icon, never colour alone. */
export function Alert({
  tone = "info",
  title,
  children,
  action,
  className,
}: {
  tone?: keyof typeof TONES;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  const { icon: Icon, box, mark } = TONES[tone];
  return (
    <div
      role={tone === "critical" ? "alert" : "status"}
      className={cx(
        "flex items-start gap-2.5 rounded-md border border-l-2 border-line px-3 py-2.5 text-[13px]",
        box,
        className,
      )}
    >
      <Icon aria-hidden className={cx("mt-px size-4 shrink-0", mark)} />
      <div className="min-w-0 flex-1 leading-snug text-ink">
        {title && <p className="font-semibold">{title}</p>}
        {children && (
          <div className={cx(!!title && "mt-0.5", "text-ink-2")}>
            {children}
          </div>
        )}
        {action && <div className="mt-2">{action}</div>}
      </div>
    </div>
  );
}

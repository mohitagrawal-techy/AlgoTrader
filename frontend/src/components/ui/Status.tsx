import type { ReactNode } from "react";

export type Tone = "good" | "warning" | "serious" | "critical" | "neutral";

const ICONS: Record<Tone, string> = {
  good: "✓",
  warning: "!",
  serious: "!",
  critical: "✕",
  neutral: "•",
};

/** Status always pairs its colour with an icon and a text label. */
export function Status({
  tone,
  children,
}: {
  tone: Tone;
  children: ReactNode;
}) {
  return (
    <span className={`status status-${tone}`}>
      <i aria-hidden>{ICONS[tone]}</i>
      {children}
    </span>
  );
}

export function Meter({ value, tone }: { value: number; tone?: Tone }) {
  const width = Math.max(0, Math.min(100, value));
  return (
    <span className={`meter ${tone ? `meter-${tone}` : ""}`}>
      <span style={{ width: `${width}%` }} />
    </span>
  );
}

export function Pnl({
  value,
  children,
}: {
  value: number | null;
  children: ReactNode;
}) {
  const direction =
    value === null || value === 0 ? "" : value > 0 ? "pnl-up" : "pnl-down";
  return <span className={direction}>{children}</span>;
}

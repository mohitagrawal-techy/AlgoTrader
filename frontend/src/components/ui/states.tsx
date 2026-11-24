import type { ReactNode } from "react";

export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children && <p>{children}</p>}
    </div>
  );
}

/** Holds a chart's place while its data loads, at the chart's own height, so nothing jumps. */
export function ChartSkeleton({
  height = 280,
  label,
}: {
  height?: number;
  label: string;
}) {
  return (
    <div
      className="chart-skeleton"
      style={{ height }}
      role="status"
      aria-live="polite"
    >
      <span className="visually-hidden">{label}</span>
    </div>
  );
}

/** A request that failed with nothing saved to show: what failed, and that it retries. */
export function LoadFailed({ what }: { what: string }) {
  return (
    <div className="empty empty-failed" role="alert">
      <strong>Couldn't load {what}</strong>
      <p>The server didn't answer. It retries automatically every minute.</p>
    </div>
  );
}

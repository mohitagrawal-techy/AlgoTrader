import { useEffect, useRef, useState, type ReactNode } from "react";

/** Wraps a live figure and flashes it green or red for a moment when it rises or falls, so a
 * tick is noticed without reading every number. Nothing moves; only the background changes. */
export function Flash({
  value,
  children,
}: {
  value: number | null | undefined;
  children: ReactNode;
}) {
  const before = useRef(value);
  const [tick, setTick] = useState<{ dir: "up" | "down"; n: number } | null>(
    null,
  );
  useEffect(() => {
    const old = before.current;
    before.current = value;
    if (old == null || value == null || old === value) return;
    setTick((t) => ({ dir: value > old ? "up" : "down", n: (t?.n ?? 0) + 1 }));
  }, [value]);
  return (
    <span
      key={tick?.n ?? 0}
      className={tick ? `flash flash-${tick.dir}` : undefined}
    >
      {children}
    </span>
  );
}

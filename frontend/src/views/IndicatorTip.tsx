import { useId, useRef, useState, type ReactNode } from "react";
import type { TechCatalog } from "../api";

type Indicator = TechCatalog["indicators"][number];

const WIDTH = 320;
const GAP = 10;

/** Wraps an indicator's name, so hovering it or focusing it shows what the indicator is and
 * its formula. The card is fixed to the window, so a scrolling list or a dialog cannot clip it. */
export function IndicatorTip({
  indicator,
  children,
  className,
}: {
  indicator: Indicator | undefined;
  children: ReactNode;
  className?: string;
}) {
  const id = useId();
  const anchor = useRef<HTMLSpanElement>(null);
  const [place, setPlace] = useState<{ top: number; left: number } | null>(null);
  if (!indicator || (!indicator.description && !indicator.formula)) {
    return <span className={className}>{children}</span>;
  }
  const show = () => {
    const box = anchor.current?.getBoundingClientRect();
    if (!box) return;
    // Beside the name where there is room, otherwise on its other side; never off screen.
    const right = box.right + GAP;
    const left = right + WIDTH <= window.innerWidth - 8 ? right : Math.max(8, box.left - GAP - WIDTH);
    const top = Math.min(Math.max(8, box.top - 4), window.innerHeight - 160);
    setPlace({ top, left });
  };
  const hide = () => setPlace(null);
  return (
    <span
      ref={anchor}
      className={`indicator-tip-anchor${className ? ` ${className}` : ""}`}
      aria-describedby={place ? id : undefined}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {children}
      {place && (
        <span id={id} role="tooltip" className="indicator-tip" style={{ top: place.top, left: place.left, width: WIDTH }}>
          <span className="indicator-tip-name">{indicator.label}</span>
          {indicator.description && <span className="indicator-tip-text">{indicator.description}</span>}
          {indicator.formula && <code className="indicator-tip-formula">{indicator.formula}</code>}
        </span>
      )}
    </span>
  );
}

/** The same, as plain text for places a card cannot go, such as a native list's options. */
export function indicatorTitle(indicator: Indicator | undefined): string | undefined {
  if (!indicator) return undefined;
  return [indicator.description, indicator.formula].filter(Boolean).join("\n") || undefined;
}

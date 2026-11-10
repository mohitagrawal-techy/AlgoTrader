import { useMemo, useState, type ReactNode } from "react";
import { AiButton, AiExplain, type AiTarget } from "./assistant";

export type Tone = "good" | "warning" | "serious" | "critical" | "neutral";

const ICONS: Record<Tone, string> = {
  good: "✓",
  warning: "!",
  serious: "!",
  critical: "✕",
  neutral: "•",
};

/** Status always pairs its colour with an icon and a text label. */
export function Status({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`status status-${tone}`}>
      <i aria-hidden>{ICONS[tone]}</i>
      {children}
    </span>
  );
}

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

export function Meter({ value, tone }: { value: number; tone?: Tone }) {
  const width = Math.max(0, Math.min(100, value));
  return (
    <span className={`meter ${tone ? `meter-${tone}` : ""}`}>
      <span style={{ width: `${width}%` }} />
    </span>
  );
}

export function Panel({
  title,
  aside,
  ai,
  children,
}: {
  title: string;
  aside?: ReactNode;
  ai?: AiTarget;
  children: ReactNode;
}) {
  const [explaining, setExplaining] = useState(false);
  // A stable target means re-renders do not restart an explanation. New settings do.
  const key = ai ? JSON.stringify([ai.component, ai.params ?? {}]) : "";
  const target = useMemo(() => ai, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <section className="panel">
      <header className="panel-header">
        <h2>{title}</h2>
        {(aside || ai) && (
          <div className="panel-aside">
            {aside}
            {ai && (
              <AiButton
                active={explaining}
                label={title}
                onClick={() => setExplaining((value) => !value)}
              />
            )}
          </div>
        )}
      </header>
      {target && explaining && <AiExplain target={target} onClose={() => setExplaining(false)} />}
      {children}
    </section>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children && <p>{children}</p>}
    </div>
  );
}

export function Pnl({ value, children }: { value: number | null; children: ReactNode }) {
  const direction = value === null || value === 0 ? "" : value > 0 ? "pnl-up" : "pnl-down";
  return <span className={direction}>{children}</span>;
}

export function Select<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <label className="select">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value as T)}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

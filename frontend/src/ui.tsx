import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { JobInfo } from "./api";
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

/** A row of mutually exclusive choices, for filters with only a few options. */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string; count?: number }[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          className={option.value === value ? "on" : ""}
          onClick={() => onChange(option.value)}
        >
          {option.label}
          {option.count !== undefined && <span className="segmented-count">{option.count}</span>}
        </button>
      ))}
    </div>
  );
}

function minutesText(ms: number): string {
  const minutes = Math.round(ms / 60000);
  if (minutes < 1) return "under a minute";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${minutes % 60} min`;
}

/** A running job: what it is doing, a progress bar, and roughly how long is left. */
export function JobStatus({ job }: { job: JobInfo }) {
  const [now, setNow] = useState(() => Date.now());
  const running = job.state === "running";
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  const progress = running ? job.progress : null;
  const share = progress && progress.total > 0 ? progress.done / progress.total : null;
  let left: string | null = null;
  if (progress && share !== null && progress.done > 0 && share < 1) {
    const spent = new Date(progress.updated_at).getTime() - new Date(progress.stage_started_at).getTime();
    if (spent > 5000) left = `about ${minutesText((spent / progress.done) * (progress.total - progress.done))} left`;
  }
  const elapsed = running && job.started_at ? minutesText(now - new Date(job.started_at).getTime()) : null;
  const tone: Tone = running ? "neutral" : job.state === "done" ? "good" : "critical";

  return (
    <div className={`job-status job-${job.state}`} role="status" aria-live="polite">
      <div className="job-line">
        <Status tone={tone}>{running ? "Working" : job.state === "done" ? "Finished" : "Failed"}</Status>
        <span className="job-message">{job.message}</span>
        {elapsed && <span className="muted job-elapsed">running {elapsed}</span>}
      </div>
      {running && (
        <>
          <div
            className={`job-bar ${share === null ? "indeterminate" : ""}`}
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={share === null ? undefined : Math.round(share * 100)}
            aria-label="Job progress"
          >
            <span style={share === null ? undefined : { width: `${Math.max(2, share * 100)}%` }} />
          </div>
          <div className="job-meta muted">
            {progress && share !== null ? (
              <>
                {progress.done.toLocaleString("en-IN")} of {progress.total.toLocaleString("en-IN")}{" "}
                {progress.unit === "replays" ? "replays" : "sessions replayed"} · {Math.floor(share * 100)}%
                {left && ` · ${left}`}
              </>
            ) : (
              "Preparing. Progress appears once the replays start."
            )}
          </div>
        </>
      )}
    </div>
  );
}

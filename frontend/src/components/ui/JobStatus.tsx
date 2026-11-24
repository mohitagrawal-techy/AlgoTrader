import { useEffect, useState } from "react";
import type { JobInfo } from "../../api";
import { Status, type Tone } from "./Status";

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
  const share =
    progress && progress.total > 0 ? progress.done / progress.total : null;
  let left: string | null = null;
  if (progress && share !== null && progress.done > 0 && share < 1) {
    const spent =
      new Date(progress.updated_at).getTime() -
      new Date(progress.stage_started_at).getTime();
    if (spent > 5000)
      left = `about ${minutesText((spent / progress.done) * (progress.total - progress.done))} left`;
  }
  const elapsed =
    running && job.started_at
      ? minutesText(now - new Date(job.started_at).getTime())
      : null;
  const tone: Tone = running
    ? "neutral"
    : job.state === "done"
      ? "good"
      : "critical";

  return (
    <div
      className={`job-status job-${job.state}`}
      role="status"
      aria-live="polite"
    >
      <div className="job-line">
        <Status tone={tone}>
          {running ? "Working" : job.state === "done" ? "Finished" : "Failed"}
        </Status>
        <span className="job-message">{job.message}</span>
        {elapsed && (
          <span className="muted job-elapsed">running {elapsed}</span>
        )}
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
            <span
              style={
                share === null
                  ? undefined
                  : { width: `${Math.max(2, share * 100)}%` }
              }
            />
          </div>
          <div className="job-meta muted">
            {progress && share !== null ? (
              <>
                {progress.done.toLocaleString("en-IN")} of{" "}
                {progress.total.toLocaleString("en-IN")}{" "}
                {progress.unit === "replays" ? "replays" : "sessions replayed"}{" "}
                · {Math.floor(share * 100)}%{left && ` · ${left}`}
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

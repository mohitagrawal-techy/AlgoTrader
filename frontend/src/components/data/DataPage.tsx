import { Download, LogIn } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type Coverage,
  type DataAction,
  type DataJob,
  type DataPayload,
} from "../../api";
import { num } from "../../format";
import {
  Alert,
  Button,
  ButtonLink,
  cx,
  Empty,
  JobStatus,
  Panel,
  Status,
  Table,
} from "../ui";

const POLL_MS = 2000;

const fullDate = (value: string | null) =>
  value
    ? new Date(`${value.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-IN", {
        day: "numeric",
        month: "short",
        year: "numeric",
        timeZone: "UTC",
      })
    : "–";

/** Days between a dataset's last session and the latest daily session anywhere. */
function behind(last: string | null, latest: string | null): number | null {
  if (!last || !latest) return null;
  return Math.round((Date.parse(latest) - Date.parse(last)) / 86400000);
}

function Freshness({
  last,
  latest,
}: {
  last: string | null;
  latest: string | null;
}) {
  const days = behind(last, latest);
  if (days === null) return <Status tone="critical">Empty</Status>;
  if (days <= 0) return <Status tone="good">Current</Status>;
  return <Status tone="warning">{days}d behind</Status>;
}

function CoverageRows({
  title,
  rows,
  latest,
}: {
  title: string;
  rows: Coverage[];
  latest: string | null;
}) {
  return (
    <>
      <tr>
        <th
          colSpan={5}
          scope="colgroup"
          className="pt-3 text-left text-[11.5px] font-medium text-ink-3"
        >
          {title}
        </th>
      </tr>
      {rows.map((row) => (
        <tr key={`${title}-${row.name}`}>
          <td>{row.name}</td>
          <td className="right">{fullDate(row.first)}</td>
          <td className="right">{fullDate(row.last)}</td>
          <td className="right">{num(row.sessions)}</td>
          <td className="right">
            <Freshness last={row.last} latest={latest} />
          </td>
        </tr>
      ))}
    </>
  );
}

function ActionRow({
  action,
  disabled,
  onRun,
}: {
  action: DataAction;
  disabled: boolean;
  onRun: (start: string | null) => void;
}) {
  const [start, setStart] = useState("");
  return (
    <li className="flex flex-col gap-2 border-t border-line px-3 py-3 first:border-t-0 sm:flex-row sm:items-center sm:gap-4">
      <div className="min-w-0 flex-1">
        <div className="text-[13.5px] font-semibold text-ink">
          {action.label}
        </div>
        <p className="m-0 max-w-[72ch] text-[12.5px] leading-relaxed text-ink-2">
          {action.description}
        </p>
      </div>
      <div className="flex shrink-0 items-end gap-2">
        {action.takes_start && (
          <label className="flex flex-col gap-1 text-[12px] text-ink-2">
            From (optional)
            <input
              type="date"
              value={start}
              onChange={(event) => setStart(event.target.value)}
              className="h-8 rounded-md border border-line bg-surface px-2 text-[13px] text-ink"
            />
          </label>
        )}
        <Button
          variant={action.plan_only ? "secondary" : "primary"}
          icon={action.plan_only ? undefined : Download}
          disabled={disabled}
          onClick={() => onRun(start || null)}
        >
          {action.plan_only ? "Count" : "Load"}
        </Button>
      </div>
    </li>
  );
}

function JobLog({ job }: { job: DataJob }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [job.log.length]);
  return (
    <div className="flex flex-col gap-2 p-3">
      <div className="text-[13px] font-semibold text-ink">{job.label}</div>
      <JobStatus job={job} />
      {job.log.length > 0 && (
        <div
          className="max-h-72 overflow-auto rounded-md border border-line bg-surface-2 p-2 font-mono text-[12px] leading-relaxed text-ink-2"
          role="log"
          aria-label="Job output"
        >
          {job.log.map((line, index) => (
            <div key={index} className="whitespace-pre-wrap">
              {line}
            </div>
          ))}
          <div ref={end} />
        </div>
      )}
    </div>
  );
}

/** What market data is stored, and loaders that fetch more from Upstox. */
export function DataPage() {
  const [data, setData] = useState<DataPayload | null>(null);
  const [job, setJob] = useState<DataJob | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .data()
      .then((found) => {
        setData(found);
        setJob(found.job);
      })
      .catch((err: Error) => setError(err.message));
  }, []);
  useEffect(load, [load]);

  const running = job?.state === "running";
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      api
        .dataJob()
        .then(({ job: latest }) => {
          setJob(latest);
          if (latest && latest.state !== "running") load();
        })
        .catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, load]);

  if (error && !data) return <Alert tone="critical">{error}</Alert>;
  if (!data) return <Empty title="Loading…" />;

  const inv = data.inventory;
  const latest = inv.daily.reduce<string | null>(
    (best, row) => (row.last && (!best || row.last > best) ? row.last : best),
    null,
  );
  const run = (action: DataAction, start: string | null) => {
    setError(null);
    api
      .dataStart(action.key, start)
      .then(setJob)
      .catch((err: Error) => setError(err.message));
  };

  return (
    <div className="flex flex-col gap-3">
      {!data.session_valid && (
        <Alert
          tone="warning"
          title="Upstox is not logged in"
          action={
            <ButtonLink icon={LogIn} href="/auth/login">
              Log in to Upstox
            </ButtonLink>
          }
        >
          Loaders fetch from Upstox, so they need a session. Only market
          holidays load without one.
        </Alert>
      )}
      {error && <Alert tone="critical">{error}</Alert>}
      <div className="grid gap-3 xl:grid-cols-12">
        <div className="flex min-w-0 flex-col gap-3 xl:col-span-5">
          <Panel
            title="Load from Upstox"
            aside={
              <span className="muted">
                One job at a time; nothing is deleted
              </span>
            }
          >
            <ul className="m-0 list-none pl-0">
              {inv.actions.map((action) => (
                <ActionRow
                  key={action.key}
                  action={action}
                  disabled={
                    running ||
                    (!data.session_valid && action.key !== "holidays")
                  }
                  onRun={(start) => run(action, start)}
                />
              ))}
            </ul>
          </Panel>
          {job && (
            <Panel title="Latest job">
              <JobLog job={job} />
            </Panel>
          )}
        </div>
        <div className="flex min-w-0 flex-col gap-3 xl:col-span-7">
          <Panel
            title="What is stored"
            aside={
              latest && (
                <span className="muted">Latest session {fullDate(latest)}</span>
              )
            }
          >
            <Table>
              <thead>
                <tr>
                  <th>Data</th>
                  <th className="right">From</th>
                  <th className="right">To</th>
                  <th className="right">Sessions</th>
                  <th className="right">
                    <span className="sr-only">Freshness</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                <CoverageRows
                  title="Daily index candles"
                  rows={inv.daily}
                  latest={latest}
                />
                <CoverageRows
                  title="One-minute index candles"
                  rows={inv.minutes}
                  latest={latest}
                />
                <CoverageRows
                  title="Daily option history"
                  rows={[{ name: "All indices", ...inv.options_daily }]}
                  latest={latest}
                />
                <CoverageRows
                  title="5-minute option bars"
                  rows={inv.options_intraday}
                  latest={latest}
                />
              </tbody>
            </Table>
            <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 px-3 py-3 text-[12.5px]">
              <dt className="text-ink-2">Instrument master</dt>
              <dd
                className={cx(
                  "m-0",
                  inv.contracts_on ? "text-ink" : "text-down",
                )}
              >
                {fullDate(inv.contracts_on)}
              </dd>
              <dt className="text-ink-2">Live chain snapshots</dt>
              <dd className="m-0 text-ink">
                {inv.chain_days} days, latest {fullDate(inv.chains_last)}
              </dd>
              <dt className="text-ink-2">Holiday list updated</dt>
              <dd className="m-0 text-ink">{fullDate(inv.holidays_updated)}</dd>
            </dl>
            <p className="panel-note">
              The collector adds each session&apos;s chains and candles after
              the close on its own. Use the loaders to reach further back or
              fill a gap.
            </p>
          </Panel>
        </div>
      </div>
    </div>
  );
}

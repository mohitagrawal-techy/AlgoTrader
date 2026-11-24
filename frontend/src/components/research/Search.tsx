import { useCallback, useEffect, useState } from "react";
import {
  api,
  type JobInfo,
  type SearchMethod,
  type SearchRecord,
  type SearchSpace,
} from "../../api";
import { num, signedInr } from "../../format";
import {
  Alert,
  Button,
  ChipCheck,
  cx,
  GroupLabel,
  JobStatus,
  NumberInput,
  Segmented,
  Status,
  Table,
} from "../ui";

const METHODS: { value: SearchMethod; label: string }[] = [
  { value: "bayesian", label: "Bayesian" },
  { value: "random", label: "Random" },
  { value: "grid", label: "Grid" },
];
const MAX_PARAMS = 4;
const POLL_MS = 2000;

const ratio = (value: number | null | undefined) =>
  value === null || value === undefined ? "–" : num(value, 2);

/** Tint a cell by its design Sharpe: green above zero, red below, stronger further out. */
function tint(score: number | null): string | undefined {
  if (score === null) return undefined;
  const share = Math.min(Math.abs(score) / 2, 1);
  const colour = score < 0 ? "var(--pnl-down)" : "var(--pnl-up)";
  return `color-mix(in srgb, ${colour} ${Math.round(8 + share * 42)}%, transparent)`;
}

/** Design-session Sharpe around the chosen setting, one cell per pair of values. */
function Heatmap({
  sensitivity,
}: {
  sensitivity: NonNullable<SearchRecord["sensitivity"]>;
}) {
  const { xs, ys, cells, center, x, y } = sensitivity;
  const cell = (i: number, j: number) =>
    cells.find((c) => c.i === i && c.j === j);
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse whitespace-nowrap font-mono text-[12px] tabular-nums [&_td]:px-2 [&_th]:px-2 sm:[&_td]:px-3 sm:[&_th]:px-3">
        <caption className="sr-only">
          Sharpe on the design sessions for each pair of settings around the
          chosen one, which is outlined.
        </caption>
        <thead>
          <tr className="border-b border-line">
            <th
              scope="col"
              className="py-2 text-left font-sans text-[11.5px] font-medium text-ink-3"
            >
              {y ? `${y.label} \\ ${x.label}` : x.label}
            </th>
            {xs.map((value) => (
              <th
                key={value}
                scope="col"
                className="py-2 text-right text-[11.5px] font-medium text-ink-3"
              >
                {num(value, x.kind === "int" ? 0 : 2)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ys.map((value, j) => (
            <tr
              key={String(value)}
              className="border-b border-grid last:border-b-0"
            >
              <th
                scope="row"
                className="py-1.5 text-left text-[11.5px] font-medium text-ink-3"
              >
                {value === null
                  ? "Sharpe"
                  : num(value, y?.kind === "int" ? 0 : 2)}
              </th>
              {xs.map((_, i) => {
                const found = cell(i, j);
                const here = i === center.i && j === center.j;
                const shown =
                  !found || !found.valid
                    ? "n/a"
                    : found.sharpe === null
                      ? "–"
                      : num(found.sharpe, 2);
                return (
                  <td
                    key={i}
                    title={
                      found?.valid
                        ? `${found.trades} trades, ${signedInr(found.net_pnl ?? 0)}`
                        : "Not a valid combination"
                    }
                    className={cx(
                      "py-1.5 text-right",
                      found?.valid ? "text-ink" : "text-ink-3",
                      here &&
                        "font-semibold outline outline-1 -outline-offset-1 outline-ink-2",
                    )}
                    style={{ background: tint(found?.score ?? null) }}
                  >
                    {shown}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SearchResult({ record }: { record: SearchRecord }) {
  const labels = Object.fromEntries(
    record.params.map((p) => [p.path, p.label]),
  );
  const values = (set: Record<string, number>) =>
    Object.entries(set)
      .map(([path, value]) => `${labels[path] ?? path} ${num(value, 2)}`)
      .join(", ");
  const sensitivity = record.sensitivity;
  return (
    <div className="flex flex-col gap-3">
      <p className="m-0 px-3 text-[13px] text-ink">
        {record.trials.length} settings searched ({record.method}),{" "}
        {record.runs} backtests run. {record.message}
      </p>
      {record.chosen_id && record.chosen_id !== record.root_id && (
        <p className="m-0 px-3 text-[13px]">
          <a href={`#lab/backtest/${encodeURIComponent(record.chosen_id)}`}>
            Open the searched strategy
          </a>
        </p>
      )}
      {sensitivity && (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2 px-3 text-[13px]">
            <span className="text-ink-2">Nearby settings</span>
            <Status tone={sensitivity.robust ? "good" : "warning"}>
              {sensitivity.robust ? "Robust" : "A lone peak"}
            </Status>
            <span className="text-ink-3">
              {Math.round(sensitivity.neighbour_share * 100)}% of neighbours
              keep half its design Sharpe
            </span>
          </div>
          <Heatmap sensitivity={sensitivity} />
        </div>
      )}
      {record.validated.length > 0 && (
        <Table>
          <thead>
            <tr>
              <th>Best on design sessions</th>
              <th className="right">Design Sharpe</th>
              <th className="right">Design trades</th>
              <th className="right">Validation Sharpe</th>
              <th className="right">Validation trades</th>
              <th className="right">Validation P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {record.validated.map((row, index) => (
              <tr key={index}>
                <td className="secondary">{values(row.values)}</td>
                <td className="right">{ratio(row.design.sharpe)}</td>
                <td className="right">{num(row.design.trades)}</td>
                <td className="right">{ratio(row.validate.sharpe)}</td>
                <td className="right">{num(row.validate.trades)}</td>
                <td className="right">{signedInr(row.validate.net_pnl)}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  );
}

/** Search several settings together on design sessions, then check the best on unseen ones. */
export function SearchPanel({
  specId,
  onFinished,
}: {
  specId: string;
  onFinished: () => void;
}) {
  const [space, setSpace] = useState<SearchSpace | null>(null);
  const [record, setRecord] = useState<SearchRecord | null>(null);
  const [method, setMethod] = useState<SearchMethod>("bayesian");
  const [chosen, setChosen] = useState<string[]>([]);
  const [trials, setTrials] = useState<number | null>(null);
  const [job, setJob] = useState<JobInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .labSearchSpace(specId)
      .then((found) => {
        setSpace(found);
        setChosen((current) =>
          current.length
            ? current
            : found.params.slice(0, 2).map((p) => p.path),
        );
        setTrials((current) => current ?? Math.min(24, found.max_trials));
        const latest = found.searches[0];
        if (latest)
          api
            .labSearch(latest.id)
            .then(setRecord)
            .catch(() => setRecord(null));
        else setRecord(null);
      })
      .catch((err: Error) => setError(err.message));
  }, [specId]);

  useEffect(load, [load]);

  const running = job?.state === "running";
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      api
        .labJob()
        .then(({ job: latest }) => {
          setJob(latest);
          if (latest && latest.state !== "running") {
            load();
            onFinished();
          }
        })
        .catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, load, onFinished]);

  if (!space) return error ? <Alert tone="critical">{error}</Alert> : null;

  const toggle = (path: string, on: boolean) =>
    setChosen((current) =>
      on
        ? [...current, path].slice(0, MAX_PARAMS)
        : current.filter((p) => p !== path),
    );
  const start = () => {
    setError(null);
    api
      .labStartSearch({
        start_id: specId,
        params: chosen,
        method,
        max_trials: Math.max(4, Math.min(trials ?? 24, space.max_trials)),
      })
      .then(() => api.labJob().then(({ job: latest }) => setJob(latest)))
      .catch((err: Error) => setError(err.message));
  };

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex flex-col gap-1.5">
        <GroupLabel id="search-settings">
          Settings to vary together, up to {MAX_PARAMS}
        </GroupLabel>
        <div
          role="group"
          aria-labelledby="search-settings"
          className="flex flex-wrap gap-1.5"
        >
          {space.params.map((param) => (
            <ChipCheck
              key={param.path}
              checked={chosen.includes(param.path)}
              onChange={(on) => toggle(param.path, on)}
            >
              {param.label}{" "}
              <span className="ml-1 font-mono text-[11.5px] text-ink-3">
                {num(param.low, param.kind === "int" ? 0 : 2)}–
                {num(param.high, param.kind === "int" ? 0 : 2)}
              </span>
            </ChipCheck>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1.5">
          <GroupLabel id="search-method">Method</GroupLabel>
          <Segmented
            label="Search method"
            value={method}
            options={METHODS}
            onChange={setMethod}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="search-trials" className="text-[12.5px] text-ink-2">
            Settings to try, at most {space.max_trials}
          </label>
          <NumberInput
            id="search-trials"
            value={trials}
            onChange={setTrials}
            className="w-24"
          />
        </div>
        <Button
          variant="primary"
          disabled={running || chosen.length === 0}
          onClick={start}
        >
          Search
        </Button>
      </div>
      <p className="m-0 text-[12px] text-ink-3">
        Each setting is backtested on the design sessions only, and every one
        counts as a trial against the deflated Sharpe. The best few are checked
        on the validation sessions; the winner is saved as a new strategy and
        given the final test once.
      </p>
      {error && <Alert tone="critical">{error}</Alert>}
      {job && <JobStatus job={job} />}
      {record && <SearchResult record={record} />}
    </div>
  );
}

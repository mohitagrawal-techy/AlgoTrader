import { useEffect, useMemo, useState } from "react";
import {
  api,
  type BacktestJob,
  type BacktestListing,
  type BacktestOptions,
  type BacktestReport,
  type BreakdownRow,
} from "../api";
import { LineChart, timeTicks } from "../charts";
import {
  PROFILE_LABELS,
  STRATEGY_LABELS,
  dateTimeIst,
  fraction,
  inr,
  inrCompact,
  num,
  shortDate,
  signedInr,
} from "../format";
import { Empty, Panel, Pnl, Select, Status, Tile, type Tone } from "../ui";

const VERDICTS: Record<string, { tone: Tone; label: string }> = {
  passed_walk_forward: { tone: "good", label: "Passed walk-forward" },
  passed_holdout: { tone: "good", label: "Passed holdout" },
  failed: { tone: "critical", label: "Failed" },
  insufficient_evidence: { tone: "warning", label: "Not enough evidence" },
  single_run: { tone: "neutral", label: "Single run, not a verdict" },
};

const KINDS: Record<string, string> = {
  walk_forward: "Walk-forward",
  backtest: "Backtest",
  holdout: "Holdout",
};

const BREAKDOWN_LABELS: Record<string, string> = {
  strategy: "Strategy",
  symbol: "Underlying",
  dte_bucket: "Days to expiry",
  iv_regime: "IV regime",
  trend: "Trend",
};

function ratio(value: number | null | undefined): string {
  return num(value, 2);
}

function fullDate(iso: string | undefined): string {
  if (!iso) return "–";
  return new Date(`${iso.slice(0, 10)}T00:00:00+05:30`).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  });
}

function Verdict({ value }: { value: string | null }) {
  const verdict = VERDICTS[value ?? ""] ?? { tone: "neutral" as Tone, label: value ?? "Unknown" };
  return <Status tone={verdict.tone}>{verdict.label}</Status>;
}

function BreakdownTable({ rows, name }: { rows: BreakdownRow[]; name: string }) {
  if (!rows.length) return <Empty title="No trades to break down" />;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>{BREAKDOWN_LABELS[name]}</th>
            <th className="right">Trades</th>
            <th className="right">Net P&amp;L</th>
            <th className="right">Win rate</th>
            <th className="right">Expectancy</th>
            <th className="right">Profit factor</th>
            <th className="right">Worst 5%</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.group}>
              <td className="primary">{STRATEGY_LABELS[row.group] ?? row.group}</td>
              <td className="right">{row.trades}</td>
              <td className="right">
                <Pnl value={row.net_pnl}>{signedInr(row.net_pnl)}</Pnl>
              </td>
              <td className="right">{fraction(row.win_rate, 0)}</td>
              <td className="right">{signedInr(row.expectancy)}</td>
              <td className="right">{ratio(row.profit_factor)}</td>
              <td className="right">{signedInr(row.tail_loss)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Report({ report }: { report: BacktestReport }) {
  const [split, setSplit] = useState("strategy");
  const { summary } = report;
  const overall = summary.overall;
  const series = useMemo(
    () => [
      {
        name: "Cumulative P&L",
        color: "var(--series-1)",
        points: summary.equity.map((point) => ({
          x: new Date(`${point.day}T15:30:00+05:30`).getTime(),
          y: point.pnl,
        })),
      },
    ],
    [summary.equity],
  );
  const formatDay = (x: number) => shortDate(new Date(x + 19800000).toISOString().slice(0, 10));
  return (
    <>
      {summary.warnings.length > 0 && (
        <div className="warning-box">
          <Status tone="warning">Read with care</Status>
          <ul>
            {summary.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="tiles tiles-6">
        <Tile label="Verdict" value={<Verdict value={report.verdict} />} hint={KINDS[report.kind]} />
        <Tile
          label="Net P&L after costs"
          value={<Pnl value={overall.net_pnl ?? 0}>{signedInr(overall.net_pnl)}</Pnl>}
          hint={`Costs ${inr(overall.costs)}`}
        />
        <Tile label="Sharpe" value={ratio(overall.sharpe)} hint={`Sortino ${ratio(overall.sortino)}`} />
        <Tile
          label="Max drawdown"
          value={inr(overall.max_drawdown)}
          hint={`Worst day ${signedInr(overall.worst_day)}`}
        />
        <Tile
          label="Trades"
          value={num(overall.trades)}
          hint={`Win rate ${fraction(overall.win_rate, 0)}`}
        />
        <Tile
          label="Trials logged"
          value={num(report.trials_to_date)}
          hint="More trials make the best result less trustworthy"
        />
      </div>

      <Panel
        title="Cumulative P&L"
        aside={
          <span className="muted">
            {report.kind === "walk_forward" ? "Unseen test windows only" : "One window"},{" "}
            {fullDate(report.window.start)} to {fullDate(report.window.end)}
          </span>
        }
      >
        {summary.equity.length < 2 ? (
          <Empty title="Not enough sessions to chart" />
        ) : (
          <LineChart
            series={series}
            polarity
            includeZero
            tickX={timeTicks}
            label="Cumulative backtest P&L after costs"
            formatX={formatDay}
            formatY={inrCompact}
            formatTipY={(y) => signedInr(y)}
          />
        )}
      </Panel>

      <div className="grid-2">
        <Panel title="Trade statistics">
          <dl className="facts facts-panel">
            <div>
              <dt>Expectancy per trade</dt>
              <dd>{signedInr(overall.expectancy)}</dd>
            </div>
            <div>
              <dt>Profit factor</dt>
              <dd>{ratio(overall.profit_factor)}</dd>
            </div>
            <div>
              <dt>Average win</dt>
              <dd>{signedInr(overall.average_win)}</dd>
            </div>
            <div>
              <dt>Average loss</dt>
              <dd>{signedInr(overall.average_loss)}</dd>
            </div>
            <div>
              <dt>Longest losing streak</dt>
              <dd>{num(overall.longest_losing_streak)}</dd>
            </div>
            <div>
              <dt>Average holding</dt>
              <dd>{num(overall.average_holding_days, 1)} days</dd>
            </div>
            <div>
              <dt>Annual return on capital</dt>
              <dd>{fraction(overall.annual_return)}</dd>
            </div>
            <div>
              <dt>Negative months</dt>
              <dd>{num(overall.negative_months_pct, 0)}%</dd>
            </div>
          </dl>
        </Panel>
        <Panel title="Robustness">
          <dl className="facts facts-panel">
            <div>
              <dt>Drawdown, typical reshuffle</dt>
              <dd>{inr(summary.monte_carlo?.drawdown_p50)}</dd>
            </div>
            <div>
              <dt>Drawdown, bad reshuffle (95th)</dt>
              <dd>{inr(summary.monte_carlo?.drawdown_p95)}</dd>
            </div>
            {report.double_slippage && (
              <>
                <div>
                  <dt>Net P&amp;L at 2x slippage</dt>
                  <dd>
                    <Pnl value={report.double_slippage.net_pnl}>
                      {signedInr(report.double_slippage.net_pnl)}
                    </Pnl>
                  </dd>
                </div>
                <div>
                  <dt>Sharpe at 2x slippage</dt>
                  <dd>{ratio(report.double_slippage.sharpe)}</dd>
                </div>
              </>
            )}
            {summary.stress_windows.map((window) => (
              <div key={window.name}>
                <dt>{window.name}</dt>
                <dd>{window.covered ? signedInr(window.net_pnl) : "Not in data"}</dd>
              </div>
            ))}
          </dl>
        </Panel>
      </div>

      <Panel
        title="Results by group"
        aside={
          <Select
            label="Group by"
            value={split}
            onChange={setSplit}
            options={Object.entries(BREAKDOWN_LABELS).map(([value, label]) => ({ value, label }))}
          />
        }
      >
        <BreakdownTable rows={summary.breakdowns[split] ?? []} name={split} />
      </Panel>

      {report.folds && (
        <Panel title="Walk-forward folds" aside={<span className="muted">{report.grid_size} settings tried per fold</span>}>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Unseen test window</th>
                  <th>Chosen on training data</th>
                  <th className="right">Train Sharpe</th>
                  <th className="right">Test Sharpe</th>
                  <th className="right">Test trades</th>
                  <th className="right">Test P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {report.folds.map((fold) => (
                  <tr key={fold.test[0]}>
                    <td>
                      {fullDate(fold.test[0])} to {fullDate(fold.test[1])}
                    </td>
                    <td className="secondary">
                      {fold.chosen
                        ? `Score ≥ ${fold.chosen.min_score}${
                            fold.chosen.disabled_profiles.length
                              ? `, without ${fold.chosen.disabled_profiles.join(", ").replaceAll("_", " ")}`
                              : ""
                          }${fold.sensitivity?.sharp_peak ? " (unstable)" : ""}`
                        : "Sat out, nothing qualified"}
                    </td>
                    <td className="right">{ratio(fold.train_sharpe)}</td>
                    <td className="right">{ratio(fold.test_sharpe)}</td>
                    <td className="right">{num(fold.test_trades)}</td>
                    <td className="right">
                      {fold.test_net_pnl === undefined ? (
                        "–"
                      ) : (
                        <Pnl value={fold.test_net_pnl}>{signedInr(fold.test_net_pnl)}</Pnl>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </>
  );
}

const JOB_POLL_MS = 3000;

/** Three months back from `last`, clipped to `first`. */
function defaultStart(first: string, last: string): string {
  const day = new Date(`${last}T00:00:00Z`);
  day.setUTCMonth(day.getUTCMonth() - 3);
  const start = day.toISOString().slice(0, 10);
  return start < first ? first : start;
}

function RunPanel({
  options,
  job,
  onStarted,
}: {
  options: BacktestOptions;
  job: BacktestJob | null | undefined;
  onStarted: (job: BacktestJob) => void;
}) {
  const single = options.single;
  const wf = options.walk_forward;
  const [mode, setMode] = useState<"walk_forward" | "backtest">(wf.ready ? "walk_forward" : "backtest");
  const [start, setStart] = useState(single.first && single.last ? defaultStart(single.first, single.last) : "");
  const [end, setEnd] = useState(single.last ?? "");
  const [minScore, setMinScore] = useState(String(single.min_scores[0] ?? 0));
  const [disabled, setDisabled] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const running = job?.state === "running";

  const run = async () => {
    setError(null);
    setSending(true);
    try {
      const started =
        mode === "walk_forward"
          ? await api.startWalkForward()
          : await api.startBacktest({ start, end, min_score: Number(minScore), disabled_profiles: disabled });
      onStarted(started);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the backtest.");
    } finally {
      setSending(false);
    }
  };

  return (
    <Panel
      title="Run a backtest"
      aside={
        running ? (
          <Status tone="neutral">
            {job.kind === "walk_forward" ? "Walk-forward" : "Backtest"} running: {job.message}
          </Status>
        ) : job?.state === "failed" ? (
          <Status tone="critical">Last run failed: {job.message}</Status>
        ) : undefined
      }
    >
      <div className="builder">
        <div className="builder-row">
          <Select
            label="Kind"
            value={mode}
            onChange={(value) => setMode(value as typeof mode)}
            options={[
              { value: "walk_forward", label: "Walk-forward (judges an edge)" },
              { value: "backtest", label: "Single window (inspect a period)" },
            ]}
          />
          {mode === "backtest" && (
            <>
              <label className="field">
                From
                <input
                  type="date"
                  value={start}
                  min={single.first ?? undefined}
                  max={single.last ?? undefined}
                  onChange={(event) => setStart(event.target.value)}
                />
              </label>
              <label className="field">
                To
                <input
                  type="date"
                  value={end}
                  min={single.first ?? undefined}
                  max={single.last ?? undefined}
                  onChange={(event) => setEnd(event.target.value)}
                />
              </label>
              <Select
                label="Minimum idea score"
                value={minScore}
                onChange={setMinScore}
                options={[0, ...single.min_scores].map((score) => ({
                  value: String(score),
                  label: score ? String(score) : "Any score",
                }))}
              />
            </>
          )}
          <button
            type="button"
            className="button"
            disabled={running || sending || (mode === "walk_forward" && !wf.ready) || (mode === "backtest" && (!start || !end))}
            onClick={run}
          >
            {running ? "Running…" : sending ? "Starting…" : "Run"}
          </button>
        </div>
        {mode === "backtest" && (
          <div className="checks run-profiles">
            <span className="muted">Leave out:</span>
            {single.profiles.map((profile) => (
              <label key={profile}>
                <input
                  type="checkbox"
                  checked={disabled.includes(profile)}
                  onChange={(event) =>
                    setDisabled(
                      event.target.checked ? [...disabled, profile] : disabled.filter((p) => p !== profile),
                    )
                  }
                />
                {PROFILE_LABELS[profile] ?? profile}
              </label>
            ))}
          </div>
        )}
        {error && <div className="banner banner-critical">{error}</div>}
        <p className="panel-note">
          {mode === "walk_forward"
            ? wf.ready
              ? `Tries ${wf.settings} settings of the idea engine on ${wf.train_sessions} sessions, tests the best on the next ${wf.test_sessions} unseen sessions, and rolls forward: ${wf.folds} folds here. The last ${wf.holdout_sessions} sessions are kept back untouched. Takes several minutes.`
              : `Needs at least ${wf.needed} sessions of history.`
            : `Replays the idea engine over the dates you pick, with ${single.first ? `history from ${fullDate(single.first)}` : "no history yet"}. Good for looking at a period; it does not show an edge, because nothing is held back.`}{" "}
          This tests the app&apos;s own idea engine. Strategies you build are tested in the Strategy lab.
        </p>
      </div>
    </Panel>
  );
}

export function BacktestsView({ listing: initial }: { listing: BacktestListing }) {
  const [listing, setListing] = useState(initial);
  const [selected, setSelected] = useState("");
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [error, setError] = useState(false);
  const job = listing.job;
  const current = listing.reports.some((r) => r.id === selected)
    ? selected
    : (listing.reports[0]?.id ?? "");

  useEffect(() => setListing(initial), [initial]);

  // While a run is going, check on it; when it finishes, open its report.
  useEffect(() => {
    if (job?.state !== "running") return;
    const timer = window.setInterval(() => {
      api
        .backtests()
        .then((body) => {
          setListing(body);
          if (body.job?.state === "done" && body.job.report_id) setSelected(body.job.report_id);
        })
        .catch(() => undefined);
    }, JOB_POLL_MS);
    return () => window.clearInterval(timer);
  }, [job?.state]);

  useEffect(() => {
    if (!current) return;
    let active = true;
    setError(false);
    api
      .backtest(current)
      .then((body) => active && setReport(body))
      .catch(() => active && setError(true));
    return () => {
      active = false;
    };
  }, [current]);

  const history = listing.history;
  const runPanel = listing.options && history.sessions > 0 && (
    <RunPanel
      options={listing.options}
      job={job}
      onStarted={(started) => setListing((current) => ({ ...current, job: started }))}
    />
  );
  if (!listing.reports.length) {
    return (
      <>
        {runPanel}
        <Empty title={history.sessions ? "No backtests have been run yet" : "No historical data imported yet"}>
          {history.sessions ? (
            <>
              {history.sessions} sessions imported, {fullDate(history.start ?? undefined)} to{" "}
              {fullDate(history.end ?? undefined)}. Start one above.
            </>
          ) : (
            <>
              Download daily F&amp;O bhavcopy files from NSE's website into a folder, then run{" "}
              <code>algoverse history-import &lt;folder&gt;</code>.
            </>
          )}
        </Empty>
      </>
    );
  }
  return (
    <>
      {runPanel}
      <div className="filters">
        <Select
          label="Report"
          value={current}
          onChange={setSelected}
          options={listing.reports.map((row) => ({
            value: row.id,
            label: `${KINDS[row.kind] ?? row.kind}, ${dateTimeIst(row.created_at)}`,
          }))}
        />
        <span className="filter-count">
          {history.sessions} sessions imported, {fullDate(history.start ?? undefined)} to{" "}
          {fullDate(history.end ?? undefined)}
        </span>
      </div>
      {error && <Empty title="This report could not be loaded" />}
      {report && report.id === current && <Report report={report} />}
    </>
  );
}

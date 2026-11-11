import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type LabOverview,
  type LabRow,
  type LabSpec,
  type LabSummary,
} from "../api";
import { LineChart, timeTicks } from "../charts";
import { dateTimeIst, fraction, inrCompact, num, shortDate, signedInr } from "../format";
import { Empty, Panel, Pnl, Select, Status, Tile, type Tone } from "../ui";

const VERDICTS: Record<string, { tone: Tone; label: string }> = {
  held_up: { tone: "good", label: "Held up on unseen data" },
  unproven: { tone: "warning", label: "Profit could be luck" },
  fails_at_2x_slippage: { tone: "warning", label: "Fails at 2x slippage" },
  failed: { tone: "critical", label: "Lost on unseen data" },
  too_few_trades: { tone: "neutral", label: "Too few trades" },
};
const SOURCES: Record<string, string> = {
  template: "Template",
  user: "Yours",
  ai: "AI",
  ladder: "Ladder",
};
const LEVELS: Record<string, string> = { simple: "Simple", moderate: "Moderate", complex: "Complex" };
const POLL_MS = 3000;

function exitText(spec: LabSpec): string {
  const exits = spec.exits;
  return `target ${Math.round(exits.target_frac * 100)}%, stop ${Math.round(
    exits.stop_frac * 100,
  )}% of premium, time exit ${exits.exit_dte}d before expiry`;
}

function LadderPanel({
  data,
  busy,
  onBuild,
  onSelect,
}: {
  data: LabOverview;
  busy: boolean;
  onBuild: (startId: string | null) => void;
  onSelect: (id: string) => void;
}) {
  const [start, setStart] = useState("");
  const report = data.ladder;
  const final = report?.final;
  const verdict = final ? VERDICTS[final.verdict] : null;
  return (
    <Panel title="Strategy ladder" aside={report ? <span className="muted">{report.trials} variations tested</span> : undefined}>
      <div className="builder">
        <p className="secondary">
          Starts with a simple strategy and adds one change at a time: a condition, a better
          structure, or a different exit. A change stays only if it does better on sessions it was
          not designed on, by more than luck explains. One winner then gets a final test on a period
          nothing has seen.
        </p>
        <div className="builder-row">
          <Select
            label="Start from"
            value={start}
            onChange={setStart}
            options={[
              { value: "", label: "Simple starters (call, put, strangle)" },
              ...data.strategies.map((row) => ({
                value: row.spec.id as string,
                label: row.spec.name,
              })),
            ]}
          />
          <button
            type="button"
            className="button"
            disabled={busy || !data.ladder_ready}
            onClick={() => onBuild(start || null)}
          >
            Build ladder
          </button>
        </div>
      </div>
      {report && (
        <div className="ladder">
          {report.lineage.length > 0 && (
            <ol className="ladder-path">
              {report.lineage.map((node) => (
                <li key={node.id}>
                  <span className={`ladder-level ${node.level}`}>{LEVELS[node.level]}</span>
                  <strong>{node.step ?? node.name}</strong>
                  <small className="secondary">
                    Unseen Sharpe {num(node.validate.sharpe, 2)}, {node.validate.trades} trades
                  </small>
                </li>
              ))}
            </ol>
          )}
          {verdict && final && (
            <div className="ladder-final">
              <Status tone={verdict.tone}>{verdict.label}</Status>
              <span>
                Final test: {final.test.trades} trades, <Pnl value={final.test.net_pnl}>{signedInr(final.test.net_pnl)}</Pnl>
                {", at 2x slippage "}
                <Pnl value={final.test_2x.net_pnl ?? 0}>{signedInr(final.test_2x.net_pnl)}</Pnl>
              </span>
              {report.winner_id && (
                <button type="button" className="link-button" onClick={() => onSelect(report.winner_id as string)}>
                  View strategy
                </button>
              )}
            </div>
          )}
          {report.final_period_uses > 1 && (
            <p className="secondary">
              The final test period has now been used {report.final_period_uses} times, so each new
              result counts for less.
            </p>
          )}
          {report.rungs.map((rung) => {
            const kept = rung.tried.filter((node) => node.accepted).length;
            return (
              <details key={rung.rung} open={rung.rung === report.rungs.length}>
                <summary>
                  Rung {rung.rung}: {kept} of {rung.tried.length} kept
                </summary>
                <div className="table-wrap">
                  <table className="table compact-table">
                    <thead>
                      <tr>
                        <th>{rung.rung === 1 ? "Strategy" : "Change"}</th>
                        <th className="right">Unseen trades</th>
                        <th className="right">Unseen P&amp;L</th>
                        <th className="right">Unseen Sharpe</th>
                        <th className="right">Chance better</th>
                        <th>Outcome</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rung.tried.map((node) => (
                        <tr key={node.id}>
                          <td className="wrap-cell">
                            <div className="primary">{node.step ?? node.name}</div>
                            <div className="secondary">{node.legs}</div>
                          </td>
                          <td className="right">{node.validate.trades}</td>
                          <td className="right">
                            <Pnl value={node.validate.net_pnl}>{signedInr(node.validate.net_pnl)}</Pnl>
                          </td>
                          <td className="right">{num(node.validate.sharpe, 2)}</td>
                          <td className="right">
                            {node.prob_better === null ? "–" : `${Math.round(node.prob_better * 100)}%`}
                          </td>
                          <td className="wrap-cell">
                            <Status tone={node.accepted ? "good" : "neutral"}>{node.accepted ? "Kept" : "Dropped"}</Status>
                            <div className="secondary">{node.reason}</div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

function Blocked({ summary }: { summary: LabSummary }) {
  const rows = Object.entries({ ...summary.blocked, ...summary.unfilled }).sort((a, b) => b[1] - a[1]);
  if (!rows.length) return <span className="secondary">No days were skipped.</span>;
  return (
    <ul className="plain-list">
      {rows.map(([reason, count]) => (
        <li key={reason}>
          <b>{count}</b> {reason}
        </li>
      ))}
    </ul>
  );
}

function Detail({
  row,
  minTrades,
  busy,
  onTest,
  onEdit,
  onDelete,
}: {
  row: LabRow;
  minTrades: number;
  busy: boolean;
  onTest: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const { spec, result } = row;
  const series = useMemo(
    () => [
      {
        name: "Test-period P&L",
        color: "var(--series-1)",
        points: (result?.equity ?? []).map((point) => ({
          x: new Date(`${point.day}T15:30:00+05:30`).getTime(),
          y: point.pnl,
        })),
      },
    ],
    [result],
  );
  const verdict = result ? VERDICTS[result.verdict] : null;
  return (
    <div className="detail">
      <header className="detail-header">
        <div>
          <div className="eyebrow">
            {SOURCES[spec.source ?? "user"]} · {LEVELS[row.level]} · {spec.bias}
            {row.undefined_risk ? " · undefined risk" : ""}
          </div>
          <h2>{spec.name}</h2>
          <div className="legs">
            {row.legs_text.split(", ").map((leg) => (
              <code key={leg}>{leg}</code>
            ))}
          </div>
        </div>
      </header>
      {spec.rationale && <p className="secondary">{spec.rationale}</p>}
      <dl className="facts facts-1">
        <div>
          <dt>Enter</dt>
          <dd>{row.entry_text}</dd>
        </div>
        <div>
          <dt>Exit</dt>
          <dd>{exitText(spec)}</dd>
        </div>
      </dl>
      <div className="builder-row">
        <button type="button" className="button" disabled={busy} onClick={onTest}>
          {result ? "Test again" : "Test"}
        </button>
        {spec.source !== "template" ? (
          <>
            <button type="button" className="button ghost" onClick={onEdit}>
              Edit
            </button>
            <button type="button" className="button ghost" onClick={onDelete}>
              Delete
            </button>
          </>
        ) : (
          <button type="button" className="button ghost" onClick={onEdit}>
            Copy and edit
          </button>
        )}
      </div>

      {!result ? (
        <Empty title="Not tested yet" />
      ) : (
        <>
          {verdict && <Status tone={verdict.tone}>{verdict.label}</Status>}
          {result.verdict === "too_few_trades" && (
            <p className="secondary">
              Fewer than {minTrades} trades in the test period, so the result says nothing either way.
            </p>
          )}
          <div className="tiles tiles-3">
            <Tile
              label="Test net P&L"
              value={<Pnl value={result.test.net_pnl}>{signedInr(result.test.net_pnl)}</Pnl>}
              hint={`${result.test.trades} trades`}
            />
            <Tile
              label="At 2x slippage"
              value={<Pnl value={result.test_2x.net_pnl ?? 0}>{signedInr(result.test_2x.net_pnl)}</Pnl>}
            />
            <Tile label="Test Sharpe" value={num(result.test.sharpe, 2)} hint={`Win rate ${fraction(result.test.win_rate, 0)}`} />
            <Tile
              label="Training net P&L"
              value={<Pnl value={result.train.net_pnl}>{signedInr(result.train.net_pnl)}</Pnl>}
              hint={`${result.train.trades} trades`}
            />
            <Tile label="Training Sharpe" value={num(result.train.sharpe, 2)} />
            <Tile label="Test max drawdown" value={signedInr(-(result.test.max_drawdown ?? 0))} />
          </div>
          <h3>
            Test period
            <small>
              {shortDate(result.windows.test_start)} to {shortDate(result.windows.test_end)}, after costs
            </small>
          </h3>
          {series[0].points.length < 2 ? (
            <Empty title="Not enough sessions to chart" />
          ) : (
            <LineChart
              series={series}
              height={200}
              polarity
              includeZero
              tickX={timeTicks}
              label={`Test-period cumulative P&L for ${spec.name}`}
              formatX={(x) => shortDate(new Date(x + 19800000).toISOString().slice(0, 10))}
              formatY={inrCompact}
              formatTipY={(y) => signedInr(y)}
            />
          )}
          <h3>Why days were skipped, test period</h3>
          <Blocked summary={result.test} />
          <p className="secondary">Tested {dateTimeIst(result.evaluated_at)}.</p>
        </>
      )}
    </div>
  );
}

export function LabView({ initialSelected }: { initialSelected: string | null }) {
  const [data, setData] = useState<LabOverview | null>(null);
  const [selected, setSelected] = useState<string | null>(initialSelected);
  const [source, setSource] = useState("all");
  const [guidance, setGuidance] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.lab());
    } catch {
      setError("Could not load the strategy lab.");
    }
  }, []);

  const running = data?.job?.state === "running";
  useEffect(() => {
    load();
    const timer = window.setInterval(load, running ? POLL_MS : POLL_MS * 5);
    return () => window.clearInterval(timer);
  }, [load, running]);

  const act = async (action: () => Promise<unknown>) => {
    setError(null);
    try {
      await action();
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The request failed.");
    }
  };

  if (!data) return <Empty title={error ?? "Loading the strategy lab…"} />;
  const rows = data.strategies.filter((row) => source === "all" || row.spec.source === source);
  const current = rows.find((row) => row.spec.id === selected) ?? rows[0] ?? null;
  const ready = data.history.ready;
  const job = data.job;

  return (
    <>
      {!ready && (
        <div className="warning-box">
          <Status tone="warning">History needed</Status>
          <p>
            Testing needs {data.history.needed} sessions of history and {data.history.sessions} are
            stored. The collector adds one session after each market close. To go further back, run{" "}
            <code>algoverse history-s3</code> to pull from your S3 bucket, or import NSE bhavcopy
            files with <code>algoverse history-import &lt;folder&gt;</code>. You can still create
            strategies now.
          </p>
        </div>
      )}
      {job && (
        <div className={job.state === "failed" ? "reject-box" : "ai-box"}>
          <Status tone={job.state === "running" ? "neutral" : job.state === "done" ? "good" : "critical"}>
            {job.state === "running" ? "Working" : job.state === "done" ? "Finished" : "Failed"}
          </Status>
          <p>{job.message}</p>
        </div>
      )}
      {error && <p className="ai-error">{error}</p>}

      <LadderPanel
        data={data}
        busy={running}
        onBuild={(startId) => act(() => api.labLadder(startId))}
        onSelect={setSelected}
      />

      <Panel
        title="Let the AI design strategies"
        aside={<span className="muted">{data.ai.model}</span>}
      >
        <div className="builder">
          <p className="secondary">
            The AI proposes {data.defaults.per_round} strategies per round for {data.defaults.rounds}{" "}
            rounds. Each is backtested at once, and the AI sees only training-period results before the
            next round. The ranking below uses a later period it never saw.
          </p>
          <div className="builder-row">
            <label className="field grow">
              <span>Optional direction for the AI</span>
              <input
                value={guidance}
                maxLength={500}
                placeholder="For example: defined-risk ideas for range-bound weeks"
                onChange={(event) => setGuidance(event.target.value)}
              />
            </label>
            <button
              type="button"
              className="button"
              disabled={running || !ready || !data.ai.enabled}
              onClick={() => act(() => api.labGenerate(guidance))}
            >
              Generate and test
            </button>
          </div>
        </div>
      </Panel>

      <div className="filters">
        <Select
          label="Show"
          value={source}
          onChange={setSource}
          options={[
            { value: "all", label: "All strategies" },
            { value: "ladder", label: "Ladder winners" },
            { value: "ai", label: "AI-designed" },
            { value: "user", label: "Yours" },
            { value: "template", label: "Templates" },
          ]}
        />
        <a className="button" href="#lab/new">
          New strategy
        </a>
        <button
          type="button"
          className="button ghost"
          disabled={running || !ready}
          onClick={() => act(() => api.labBacktest(null))}
        >
          Test all
        </button>
        <span className="filter-count">
          {data.tested} of {data.strategies.length} tested · {data.trials_logged} trials logged
        </span>
      </div>

      <div className="split">
        <Panel
          title="Leaderboard"
          aside={<span className="muted">Ranked on the unseen test period, after costs</span>}
        >
          {rows.length === 0 ? (
            <Empty title="No strategies here yet" />
          ) : (
            <div className="table-wrap">
              <table className="table table-ideas">
                <thead>
                  <tr>
                    <th>Strategy</th>
                    <th>Verdict</th>
                    <th className="right">Test trades</th>
                    <th className="right">Test P&amp;L</th>
                    <th className="right">At 2x slippage</th>
                    <th className="right">Test Sharpe</th>
                    <th className="right">Training P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const result = row.result;
                    const verdict = result ? VERDICTS[result.verdict] : null;
                    return (
                      <tr
                        key={row.spec.id}
                        className={row.spec.id === current?.spec.id ? "selected" : ""}
                        onClick={() => setSelected(row.spec.id as string)}
                        tabIndex={0}
                        onKeyDown={(event) => event.key === "Enter" && setSelected(row.spec.id as string)}
                      >
                        <td className="wrap-cell">
                          <div className="primary">{row.spec.name}</div>
                          <div className="secondary">
                            {SOURCES[row.spec.source ?? "user"]} · {LEVELS[row.level]} · {row.legs_text}
                          </div>
                        </td>
                        <td>
                          {verdict ? (
                            <Status tone={verdict.tone}>{verdict.label}</Status>
                          ) : (
                            <span className="secondary">Not tested</span>
                          )}
                        </td>
                        <td className="right">{result ? result.test.trades : "–"}</td>
                        <td className="right">
                          {result ? <Pnl value={result.test.net_pnl}>{signedInr(result.test.net_pnl)}</Pnl> : "–"}
                        </td>
                        <td className="right">
                          {result ? (
                            <Pnl value={result.test_2x.net_pnl ?? 0}>{signedInr(result.test_2x.net_pnl)}</Pnl>
                          ) : (
                            "–"
                          )}
                        </td>
                        <td className="right">{result ? num(result.test.sharpe, 2) : "–"}</td>
                        <td className="right">
                          {result ? <Pnl value={result.train.net_pnl}>{signedInr(result.train.net_pnl)}</Pnl> : "–"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p className="panel-note">
            The more strategies are tested, the more likely the top one is there by luck. Treat a
            leader as a candidate for forward testing, not as proof.
          </p>
        </Panel>
        <aside className="panel detail-panel">
          {current && (
            <Detail
              row={current}
              minTrades={data.min_test_trades}
              busy={running || !ready}
              onTest={() => act(() => api.labBacktest([current.spec.id as string]))}
              onEdit={() => {
                const action = current.spec.source === "template" ? "copy" : "edit";
                window.location.hash = `lab/${action}/${current.spec.id}`;
              }}
              onDelete={() => act(() => api.labDelete(current.spec.id as string))}
            />
          )}
        </aside>
      </div>
    </>
  );
}

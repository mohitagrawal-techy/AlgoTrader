import { useMemo, useState } from "react";
import type { DriftCheck, ForwardOverview, ForwardRun, ShadowRow } from "../api";
import { LineChart, timeTicks } from "../charts";
import { dateTimeIst, fraction, inr, inrCompact, num, signedInr } from "../format";
import { Empty, Panel, Pnl, Select, Status, Tile, type Tone } from "../ui";

const STAGE_LABELS: Record<string, string> = {
  ai_veto: "Vetoed by AI",
  ai_approved: "Reviewed by AI, not vetoed",
  risk: "Rejected by risk limits",
  filter: "Rejected by filters",
  passed_over: "Tradable, not selected",
};

const DRIFT_STATUS: Record<string, { tone: Tone; label: string }> = {
  in: { tone: "good", label: "In range" },
  out: { tone: "critical", label: "Outside range" },
  insufficient_data: { tone: "neutral", label: "Too few samples" },
  no_baseline: { tone: "neutral", label: "No backtest attached" },
};

function runStatus(run: ForwardRun): { tone: Tone; label: string } {
  if (run.promoted_at) return { tone: "good", label: "Live-trading candidate" };
  if (run.status === "halted") return { tone: "critical", label: "Halted" };
  if (run.status === "ended") return { tone: "neutral", label: "Ended" };
  return { tone: "good", label: "Running" };
}

function formatDrift(value: number | null, unit: string): string {
  if (value === null) return "–";
  if (unit === "money") return signedInr(value);
  if (unit === "loss") return inr(value);
  if (unit === "fraction") return fraction(value, 0);
  return `${num(value, 2)}x`;
}

function rangeText(check: DriftCheck): string {
  if (check.low === null && check.high === null) return "–";
  if (check.low === null) return `At most ${formatDrift(check.high, check.unit)}`;
  return `${formatDrift(check.low, check.unit)} to ${formatDrift(check.high, check.unit)}`;
}

function Gates({ run }: { run: ForwardRun }) {
  const passed = run.gates.filter((gate) => gate.passed).length;
  return (
    <Panel
      title="Promotion gate"
      aside={
        <span className="muted">
          {passed} of {run.gates.length} met. Passing never enables live orders.
        </span>
      }
    >
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Requirement</th>
              <th>Needed</th>
              <th>Now</th>
              <th>Result</th>
            </tr>
          </thead>
          <tbody>
            {run.gates.map((gate) => (
              <tr key={gate.key}>
                <td className="primary">{gate.label}</td>
                <td className="secondary">{gate.required}</td>
                <td>{gate.actual}</td>
                <td>
                  <Status tone={gate.passed ? "good" : "neutral"}>
                    {gate.passed ? "Met" : "Not yet"}
                  </Status>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function Drift({ run }: { run: ForwardRun }) {
  return (
    <Panel title="Forward versus backtest">
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Measure</th>
              <th className="right">Forward</th>
              <th className="right">Expected</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {run.drift.map((check) => {
              const status = DRIFT_STATUS[check.status];
              return (
                <tr key={check.key}>
                  <td className="primary">{check.label}</td>
                  <td className="right">{formatDrift(check.actual, check.unit)}</td>
                  <td className="right">{rangeText(check)}</td>
                  <td>
                    <Status tone={status.tone}>{status.label}</Status>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function Shadow({ rows, run }: { rows: ShadowRow[]; run: ForwardRun }) {
  const ai = run.ai_value;
  return (
    <Panel
      title="Ideas not taken, followed at one lot"
      aside={<span className="muted">Hypothetical results after costs</span>}
    >
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Decided by</th>
              <th className="right">Closed</th>
              <th className="right">Following</th>
              <th className="right">Did not fill</th>
              <th className="right">Win rate</th>
              <th className="right">Expectancy</th>
              <th className="right">Net P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.stage}>
                <td className="primary">{STAGE_LABELS[row.stage]}</td>
                <td className="right">{row.trades}</td>
                <td className="right">{row.following}</td>
                <td className="right">{row.unfilled}</td>
                <td className="right">{row.trades ? fraction(row.win_rate, 0) : "–"}</td>
                <td className="right">{row.trades ? signedInr(row.expectancy) : "–"}</td>
                <td className="right">
                  {row.trades ? (
                    <Pnl value={row.net_pnl ?? 0}>{signedInr(row.net_pnl)}</Pnl>
                  ) : (
                    "–"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="panel-note">
        {ai.enough_data
          ? `Ideas the AI let through made ${signedInr(ai.benefit_per_trade)} more per trade than ideas it vetoed.`
          : `Too few samples to say whether the AI adds value: ${ai.approved_trades} reviewed and ${ai.vetoed_trades} vetoed ideas have closed.`}
      </p>
    </Panel>
  );
}

function RunDetail({ run }: { run: ForwardRun }) {
  const status = runStatus(run);
  const stats = run.stats;
  const series = useMemo(
    () => [
      {
        name: "Total P&L",
        color: "var(--series-1)",
        points: run.equity.map((point) => ({ x: new Date(point.ts).getTime(), y: point.pnl })),
      },
    ],
    [run.equity],
  );
  const spanMs = series[0].points.length
    ? series[0].points[series[0].points.length - 1].x - series[0].points[0].x
    : 0;
  const formatX = (x: number) => {
    const label = dateTimeIst(new Date(x).toISOString());
    return spanMs > 7 * 86400000 ? label.split(",")[0] : label;
  };
  return (
    <>
      {run.status === "active" && !run.locked_inputs_current && (
        <div className="warning-box">
          <Status tone="warning">Locked inputs changed</Status>
          <p>This run will end at the next collection cycle and restart as a new version.</p>
        </div>
      )}
      {run.end_reason && (
        <div className="warning-box">
          <Status tone={status.tone}>{status.label}</Status>
          <p>{run.end_reason}</p>
        </div>
      )}
      <div className="tiles tiles-6">
        <Tile label="Status" value={<Status tone={status.tone}>{status.label}</Status>} hint={`${run.role}, version ${run.version}`} />
        <Tile
          label="Net P&L after costs"
          value={<Pnl value={stats.net_pnl ?? 0}>{signedInr(stats.net_pnl ?? 0)}</Pnl>}
          hint={`${num(run.weeks, 1)} weeks running`}
        />
        <Tile label="Closed trades" value={num(stats.trades ?? 0)} hint={`${run.live_positions} live now`} />
        <Tile label="Expectancy" value={signedInr(stats.expectancy)} hint={`Win rate ${fraction(stats.win_rate, 0)}`} />
        <Tile label="Max drawdown" value={signedInr(-run.drawdown)} hint={`Halts at ${run.gate_limits.max_drawdown_pct}% of capital`} />
        <Tile
          label="Score threshold"
          value={num(run.min_score, 0)}
          hint={
            run.disabled_profiles.length
              ? `Without ${run.disabled_profiles.join(", ").replaceAll("_", " ")}`
              : "All styles"
          }
        />
      </div>
      <Panel title="P&L over time" aside={<span className="muted">Started {dateTimeIst(run.started_at)}</span>}>
        {run.equity.length < 2 ? (
          <Empty title="Not enough history to chart" />
        ) : (
          <LineChart
            series={series}
            polarity
            includeZero
            tickX={timeTicks}
            label="Forward test P&L over time"
            formatX={formatX}
            formatY={inrCompact}
            formatTipX={(x) => dateTimeIst(new Date(x).toISOString())}
            formatTipY={(y) => signedInr(y, 2)}
          />
        )}
      </Panel>
      <div className="grid-2">
        <Gates run={run} />
        <Drift run={run} />
      </div>
      <Shadow rows={run.shadow} run={run} />
    </>
  );
}

function Comparison({ runs }: { runs: ForwardRun[] }) {
  if (runs.length < 2) return null;
  return (
    <Panel title="Active runs, same market data, separate capital">
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Run</th>
              <th>Role</th>
              <th className="right">Closed</th>
              <th className="right">Net P&amp;L</th>
              <th className="right">Expectancy</th>
              <th className="right">Sharpe</th>
              <th className="right">Drawdown</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td className="primary">
                  {run.name} v{run.version}
                </td>
                <td>{run.role}</td>
                <td className="right">{run.stats.trades ?? 0}</td>
                <td className="right">
                  <Pnl value={run.stats.net_pnl ?? 0}>{signedInr(run.stats.net_pnl ?? 0)}</Pnl>
                </td>
                <td className="right">{signedInr(run.stats.expectancy)}</td>
                <td className="right">{num(run.stats.sharpe, 2)}</td>
                <td className="right">{signedInr(-run.drawdown)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

export function ForwardView({ overview }: { overview: ForwardOverview }) {
  const [selected, setSelected] = useState("");
  const runs = overview.runs;
  if (!runs.length) {
    return (
      <Empty title="No forward runs yet">
        Run <code>algoverse forward-start --name baseline --role champion</code>, then leave{" "}
        <code>algoverse collect</code> running during market hours.
      </Empty>
    );
  }
  const current = runs.find((run) => String(run.id) === selected) ?? runs[0];
  return (
    <>
      <div className="filters">
        <Select
          label="Run"
          value={String(current.id)}
          onChange={setSelected}
          options={runs.map((run) => ({
            value: String(run.id),
            label: `${run.name} v${run.version} (${run.status})`,
          }))}
        />
      </div>
      <Comparison runs={runs.filter((run) => run.status === "active")} />
      <RunDetail run={current} key={current.id} />
    </>
  );
}

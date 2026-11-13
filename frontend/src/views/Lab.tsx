import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type AutopilotCycle,
  type AutopilotSettings,
  type LabOverview,
  type LabRow,
  type LabSummary,
  type TechCatalog,
} from "../api";
import { LineChart, timeTicks } from "../charts";
import { StrategyVisual } from "./StrategyVisual";
import {
  dateTimeIst,
  fraction,
  inrCompact,
  num,
  shortDate,
  signedInr,
} from "../format";
import {
  Empty,
  JobStatus,
  Panel,
  Pnl,
  Segmented,
  LabeledSelect,
  Status,
  Tile,
  type Tone,
  Alert,
  Button,
  ButtonLink,
  Field,
  Table,
  TextInput,
  Tiles,
} from "../components/ui";

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
const LEVELS: Record<string, string> = {
  simple: "Simple",
  moderate: "Moderate",
  complex: "Complex",
};
const POLL_MS = 3000;
const SOURCE_FILTERS = [
  { value: "all", label: "All" },
  { value: "user", label: "Yours" },
  { value: "ai", label: "AI" },
  { value: "ladder", label: "Ladder" },
  { value: "template", label: "Templates" },
];

const AUTOPILOT_LIMITS: Record<
  keyof Omit<AutopilotSettings, "guidance">,
  [number, number]
> = {
  cycles: [1, 200],
  hours: [1, 24],
  per_cycle: [1, 8],
  tune_rounds: [1, 15],
};

function clampSetting(value: number, [low, high]: [number, number]): number {
  return Number.isFinite(value)
    ? Math.min(high, Math.max(low, Math.round(value)))
    : low;
}

function CycleRow({ cycle }: { cycle: AutopilotCycle }) {
  const best = cycle.designed.reduce<AutopilotCycle["designed"][number] | null>(
    (top, row) =>
      (row.train?.sharpe ?? -99) > (top?.train?.sharpe ?? -99) ? row : top,
    null,
  );
  const verdict = cycle.final ? VERDICTS[cycle.final.verdict] : null;
  const running = !cycle.finished_at && !cycle.message;
  return (
    <tr>
      <td className="right">{cycle.number}</td>
      <td className="wrap-cell">
        <div className="primary">
          {cycle.designed.length} designed
          {cycle.dropped ? `, ${cycle.dropped} dropped` : ""}
        </div>
        {best?.train && (
          <div className="secondary">
            Best on training: {best.name}, Sharpe {num(best.train.sharpe, 2)}
          </div>
        )}
      </td>
      <td className="wrap-cell">
        {cycle.picked ? (
          cycle.session_id ? (
            <a href={`#lab/tune/s/${encodeURIComponent(cycle.session_id)}`}>
              {cycle.picked.name}
            </a>
          ) : (
            <span>{cycle.picked.name}</span>
          )
        ) : (
          <span className="muted">–</span>
        )}
      </td>
      <td className="wrap-cell">
        {verdict ? (
          <>
            <Status tone={verdict.tone}>{verdict.label}</Status>
            {cycle.final?.test && (
              <div className="secondary">
                {cycle.final.test.trades} trades,{" "}
                <Pnl value={cycle.final.test.net_pnl}>
                  {signedInr(cycle.final.test.net_pnl)}
                </Pnl>
              </div>
            )}
          </>
        ) : (
          <span className="secondary">
            {cycle.message ?? (running ? "Working…" : "–")}
          </span>
        )}
      </td>
      <td className="wrap-cell">
        {cycle.kept.length ? (
          cycle.kept.map((k) => <div key={k.id}>{k.name}</div>)
        ) : (
          <span className="muted">None</span>
        )}
        {cycle.archived > 0 && (
          <div className="secondary">{cycle.archived} archived</div>
        )}
      </td>
    </tr>
  );
}

function AutopilotPanel({
  data,
  busy,
  onStart,
  onStop,
  onSelect,
}: {
  data: LabOverview;
  busy: boolean;
  onStart: (settings: AutopilotSettings) => void;
  onStop: () => void;
  onSelect: (id: string) => void;
}) {
  const state = data.autopilot;
  const [settings, setSettings] = useState<AutopilotSettings>(
    () =>
      state?.settings ?? {
        cycles: 20,
        hours: 8,
        per_cycle: 4,
        tune_rounds: 3,
        guidance: "",
      },
  );
  const live = data.job?.kind === "autopilot" && data.job.state === "running";
  const cycles = state ? [...state.cycles].reverse() : [];
  const kept = state?.cycles.flatMap((c) => c.kept) ?? [];
  const designed =
    state?.cycles.reduce((sum, c) => sum + c.designed.length, 0) ?? 0;
  const number = (
    key: keyof typeof AUTOPILOT_LIMITS,
    label: string,
    hint: string,
  ) => (
    <Field label={label} hint={hint}>
      <TextInput
        small
        type="number"
        narrow
        min={AUTOPILOT_LIMITS[key][0]}
        max={AUTOPILOT_LIMITS[key][1]}
        value={settings[key]}
        disabled={live}
        onChange={(e) =>
          setSettings({
            ...settings,
            [key]: clampSetting(Number(e.target.value), AUTOPILOT_LIMITS[key]),
          })
        }
      />
    </Field>
  );

  return (
    <Panel
      title="Autopilot"
      aside={<span className="muted">{data.archived} archived so far</span>}
    >
      <div className="builder">
        <p className="secondary">
          Runs on its own until it reaches a limit or you stop it. Each cycle
          the AI designs new strategies, the best one on training data is tuned
          one setting at a time, and the result gets a final test on a period
          nothing has seen. Strategies that held up stay in the lab. The rest
          are moved to the archive, which the AI still learns from. The more it
          tries, the stricter the luck check gets, so a long run is not a way to
          force a pass.
        </p>
        <div className="builder-row autopilot-settings">
          {number("cycles", "Cycles", "1 to 200")}
          {number("hours", "Hours at most", "1 to 24")}
          {number("per_cycle", "Designs per cycle", "1 to 8")}
          {number("tune_rounds", "Tuning rounds", "1 to 15")}
        </div>
        <div className="builder-row">
          <Field label="Optional direction for the AI" className="field-grow">
            <TextInput
              small
              value={settings.guidance}
              maxLength={500}
              disabled={live}
              placeholder="For example: intraday Nifty ideas with defined risk"
              onChange={(e) =>
                setSettings({ ...settings, guidance: e.target.value })
              }
            />
          </Field>
          {live ? (
            <Button onClick={onStop}>Stop after this step</Button>
          ) : (
            <Button
              variant="primary"
              disabled={
                busy ||
                !data.history.ready ||
                !data.ladder_ready ||
                !data.ai.enabled
              }
              onClick={() => onStart(settings)}
            >
              Start autopilot
            </Button>
          )}
        </div>
        {!data.ai.enabled && (
          <p className="secondary">
            The AI model is turned off, so autopilot cannot run.
          </p>
        )}
      </div>

      {state && (
        <div className="autopilot-log">
          <p className="autopilot-summary">
            <Status tone={state.running ? "neutral" : "good"}>
              {state.running ? "Running" : "Stopped"}
            </Status>
            <span>
              {state.cycles.length} of {state.settings.cycles} cycles,{" "}
              {designed} strategies designed, {kept.length} kept. Started{" "}
              {dateTimeIst(state.started_at)}.
              {state.ended && ` ${state.ended}.`.replace("..", ".")}
            </span>
          </p>
          {kept.length > 0 && (
            <div className="autopilot-kept">
              <span className="muted">Kept so far:</span>
              {kept.map((k) => (
                <Button
                  variant="link"
                  key={k.id}
                  onClick={() => onSelect(k.id)}
                >
                  {k.name}
                </Button>
              ))}
            </div>
          )}
          {cycles.length > 0 && (
            <Table compact>
              <thead>
                <tr>
                  <th className="right">Cycle</th>
                  <th>Designed</th>
                  <th>Tuned</th>
                  <th>Final test</th>
                  <th>Kept</th>
                </tr>
              </thead>
              <tbody>
                {cycles.map((cycle) => (
                  <CycleRow key={cycle.number} cycle={cycle} />
                ))}
              </tbody>
            </Table>
          )}
        </div>
      )}
    </Panel>
  );
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
    <Panel
      title="Strategy ladder"
      aside={
        report ? (
          <span className="muted">{report.trials} variations tested</span>
        ) : undefined
      }
    >
      <div className="builder">
        <p className="secondary">
          Starts with a simple strategy and adds one change at a time: a
          condition, a better structure, or a different exit. A change stays
          only if it does better on sessions it was not designed on, by more
          than luck explains. One winner then gets a final test on a period
          nothing has seen.
        </p>
        <div className="builder-row">
          <LabeledSelect
            stacked
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
          <Button
            variant="primary"
            disabled={busy || !data.ladder_ready}
            onClick={() => onBuild(start || null)}
          >
            Build ladder
          </Button>
        </div>
      </div>
      {report && (
        <div className="ladder">
          {report.lineage.length > 0 && (
            <ol className="ladder-path">
              {report.lineage.map((node) => (
                <li key={node.id}>
                  <span className={`ladder-level ${node.level}`}>
                    {LEVELS[node.level]}
                  </span>
                  <strong>{node.step ?? node.name}</strong>
                  <small className="secondary">
                    Unseen Sharpe {num(node.validate.sharpe, 2)},{" "}
                    {node.validate.trades} trades
                  </small>
                </li>
              ))}
            </ol>
          )}
          {verdict && final && (
            <div className="ladder-final">
              <Status tone={verdict.tone}>{verdict.label}</Status>
              <span>
                Final test: {final.test.trades} trades,{" "}
                <Pnl value={final.test.net_pnl}>
                  {signedInr(final.test.net_pnl)}
                </Pnl>
                {", at 2x slippage "}
                <Pnl value={final.test_2x.net_pnl ?? 0}>
                  {signedInr(final.test_2x.net_pnl)}
                </Pnl>
              </span>
              {report.winner_id && (
                <Button
                  variant="link"
                  onClick={() => onSelect(report.winner_id as string)}
                >
                  View strategy
                </Button>
              )}
            </div>
          )}
          {report.final_period_uses > 1 && (
            <p className="secondary">
              The final test period has now been used {report.final_period_uses}{" "}
              times, so each new result counts for less.
            </p>
          )}
          <details className="ladder-report">
            <summary>Every change tried, rung by rung</summary>
            {report.rungs.map((rung) => {
              const kept = rung.tried.filter((node) => node.accepted).length;
              return (
                <details key={rung.rung}>
                  <summary>
                    Rung {rung.rung}: {kept} of {rung.tried.length} kept
                  </summary>
                  <Table compact>
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
                            <div className="primary">
                              {node.step ?? node.name}
                            </div>
                            <div className="secondary">{node.legs}</div>
                          </td>
                          <td className="right">{node.validate.trades}</td>
                          <td className="right">
                            <Pnl value={node.validate.net_pnl}>
                              {signedInr(node.validate.net_pnl)}
                            </Pnl>
                          </td>
                          <td className="right">
                            {num(node.validate.sharpe, 2)}
                          </td>
                          <td className="right">
                            {node.prob_better === null
                              ? "–"
                              : `${Math.round(node.prob_better * 100)}%`}
                          </td>
                          <td className="wrap-cell">
                            <Status tone={node.accepted ? "good" : "neutral"}>
                              {node.accepted ? "Kept" : "Dropped"}
                            </Status>
                            <div className="secondary">{node.reason}</div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                </details>
              );
            })}
          </details>
        </div>
      )}
    </Panel>
  );
}

function Blocked({ summary }: { summary: LabSummary }) {
  const rows = Object.entries({ ...summary.blocked, ...summary.unfilled }).sort(
    (a, b) => b[1] - a[1],
  );
  if (!rows.length)
    return <span className="secondary">No days were skipped.</span>;
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
  catalog,
  signals,
  minTrades,
  busy,
  onTest,
  onEdit,
  onDelete,
}: {
  row: LabRow;
  catalog: TechCatalog;
  signals: { name: string; label: string }[];
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
      <details className="sv-details" open>
        <summary>Strategy map</summary>
        <StrategyVisual spec={spec} catalog={catalog} signals={signals} />
      </details>
      <div className="detail-actions">
        {result && spec.id ? (
          <ButtonLink
            variant="primary"
            href={`#lab/backtest/${encodeURIComponent(spec.id)}`}
          >
            Full backtest
          </ButtonLink>
        ) : null}
        <Button
          variant={result ? "secondary" : "primary"}
          disabled={busy}
          onClick={onTest}
        >
          {result ? "Test again" : "Test"}
        </Button>
        {spec.id && (
          <ButtonLink href={`#lab/tune/from/${encodeURIComponent(spec.id)}`}>
            Tune
          </ButtonLink>
        )}
        <Button onClick={onEdit}>
          {spec.source !== "template" ? "Edit" : "Copy and edit"}
        </Button>
        {spec.source !== "template" && (
          <Button variant="link-danger" className="push-end" onClick={onDelete}>
            Delete
          </Button>
        )}
      </div>

      {!result ? (
        <Empty title="Not tested yet" />
      ) : (
        <>
          {verdict && <Status tone={verdict.tone}>{verdict.label}</Status>}
          {result.verdict === "too_few_trades" && (
            <p className="secondary">
              Fewer than {minTrades} trades in the test period, so the result
              says nothing either way.
            </p>
          )}
          <Tiles columns={3}>
            <Tile
              label="Test net P&L"
              value={
                <Pnl value={result.test.net_pnl}>
                  {signedInr(result.test.net_pnl)}
                </Pnl>
              }
              hint={`${result.test.trades} trades`}
            />
            <Tile
              label="At 2x slippage"
              value={
                <Pnl value={result.test_2x.net_pnl ?? 0}>
                  {signedInr(result.test_2x.net_pnl)}
                </Pnl>
              }
            />
            <Tile
              label="Test Sharpe"
              value={num(result.test.sharpe, 2)}
              hint={`Win rate ${fraction(result.test.win_rate, 0)}`}
            />
            <Tile
              label="Training net P&L"
              value={
                <Pnl value={result.train.net_pnl}>
                  {signedInr(result.train.net_pnl)}
                </Pnl>
              }
              hint={`${result.train.trades} trades`}
            />
            <Tile label="Training Sharpe" value={num(result.train.sharpe, 2)} />
            <Tile
              label="Test max drawdown"
              value={signedInr(-(result.test.max_drawdown ?? 0))}
            />
          </Tiles>
          <h3>
            Test period
            <small>
              {shortDate(result.windows.test_start)} to{" "}
              {shortDate(result.windows.test_end)}, after costs
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
              formatX={(x) =>
                shortDate(new Date(x + 19800000).toISOString().slice(0, 10))
              }
              formatY={inrCompact}
              formatTipY={(y) => signedInr(y)}
            />
          )}
          <h3>Why days were skipped, test period</h3>
          <Blocked summary={result.test} />
          <p className="secondary">
            Tested {dateTimeIst(result.evaluated_at)}.
          </p>
        </>
      )}
    </div>
  );
}

export function LabView({
  initialSelected,
}: {
  initialSelected: string | null;
}) {
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
      setError(
        reason instanceof Error ? reason.message : "The request failed.",
      );
    }
  };

  if (!data) return <Empty title={error ?? "Loading the strategy lab…"} />;
  const rows = data.strategies.filter(
    (row) => source === "all" || row.spec.source === source,
  );
  const current =
    rows.find((row) => row.spec.id === selected) ?? rows[0] ?? null;
  const ready = data.history.ready;
  const job = data.job;

  return (
    <>
      {!ready && (
        <Alert tone="warning" title="History needed">
          <p>
            Testing needs {data.history.needed} sessions of history and{" "}
            {data.history.sessions} are stored. The collector adds one session
            after each market close. To go further back, run{" "}
            <code>algoverse history-s3</code> to pull from your S3 bucket, or
            import NSE bhavcopy files with{" "}
            <code>algoverse history-import &lt;folder&gt;</code>. You can still
            create strategies now.
          </p>
        </Alert>
      )}
      {job && <JobStatus job={job} />}
      {error && <Alert tone="critical">{error}</Alert>}

      <div className="toolbar">
        <Segmented
          label="Show"
          value={source}
          onChange={setSource}
          options={SOURCE_FILTERS.map((option) => ({
            ...option,
            count:
              option.value === "all"
                ? data.strategies.length
                : data.strategies.filter(
                    (row) => row.spec.source === option.value,
                  ).length,
          }))}
        />
        <span className="toolbar-note">
          {data.tested} of {data.strategies.length} tested, {data.trials_logged}{" "}
          trials logged
        </span>
        <div className="toolbar-actions">
          <Button
            disabled={running || !ready}
            onClick={() => act(() => api.labBacktest(null))}
          >
            Test all
          </Button>
          <ButtonLink href="#lab/tune">Tune a strategy</ButtonLink>
          <ButtonLink variant="primary" href="#lab/new">
            New strategy
          </ButtonLink>
        </div>
      </div>

      <div className="split">
        <Panel
          title="Leaderboard"
          aside={
            <span className="muted">
              Ranked on the unseen test period, after costs
            </span>
          }
        >
          {rows.length === 0 ? (
            <Empty title="No strategies here yet" />
          ) : (
            <Table className="table-ideas leaderboard">
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
                      className={
                        row.spec.id === current?.spec.id ? "selected" : ""
                      }
                      onClick={() => setSelected(row.spec.id as string)}
                      tabIndex={0}
                      onKeyDown={(event) =>
                        event.key === "Enter" &&
                        setSelected(row.spec.id as string)
                      }
                    >
                      <td className="wrap-cell">
                        <div className="primary">{row.spec.name}</div>
                        <div className="secondary">
                          {SOURCES[row.spec.source ?? "user"]} ·{" "}
                          {LEVELS[row.level]} · {row.legs_text}
                        </div>
                      </td>
                      <td>
                        {verdict ? (
                          <Status tone={verdict.tone}>{verdict.label}</Status>
                        ) : (
                          <span className="secondary">Not tested</span>
                        )}
                      </td>
                      <td className="right">
                        {result ? result.test.trades : "–"}
                      </td>
                      <td className="right">
                        {result ? (
                          <Pnl value={result.test.net_pnl}>
                            {signedInr(result.test.net_pnl)}
                          </Pnl>
                        ) : (
                          "–"
                        )}
                      </td>
                      <td className="right">
                        {result ? (
                          <Pnl value={result.test_2x.net_pnl ?? 0}>
                            {signedInr(result.test_2x.net_pnl)}
                          </Pnl>
                        ) : (
                          "–"
                        )}
                      </td>
                      <td className="right">
                        {result ? num(result.test.sharpe, 2) : "–"}
                      </td>
                      <td className="right">
                        {result ? (
                          <Pnl value={result.train.net_pnl}>
                            {signedInr(result.train.net_pnl)}
                          </Pnl>
                        ) : (
                          "–"
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          )}
          <p className="panel-note">
            The more strategies are tested, the more likely the top one is there
            by luck. Treat a leader as a candidate for forward testing, not as
            proof.
          </p>
        </Panel>
        <aside className="panel detail-panel">
          {current && (
            <Detail
              row={current}
              catalog={data.technical}
              signals={data.signals}
              minTrades={data.min_test_trades}
              busy={running || !ready}
              onTest={() =>
                act(() => api.labBacktest([current.spec.id as string]))
              }
              onEdit={() => {
                const action =
                  current.spec.source === "template" ? "copy" : "edit";
                window.location.hash = `lab/${action}/${current.spec.id}`;
              }}
              onDelete={() =>
                act(() => api.labDelete(current.spec.id as string))
              }
            />
          )}
        </aside>
      </div>

      <section className="lab-tools" aria-label="Find strategies automatically">
        <header className="section-head">
          <h2>Find strategies automatically</h2>
          <p className="secondary">
            Each judges every candidate on sessions it was not built on, then
            spends the final test period once.
          </p>
        </header>
        <AutopilotPanel
          data={data}
          busy={running}
          onStart={(settings) => act(() => api.labAutopilot(settings))}
          onStop={() => act(() => api.labAutopilotStop())}
          onSelect={setSelected}
        />
        <div className="lab-tools-grid">
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
                The AI proposes {data.defaults.per_round} strategies per round
                for {data.defaults.rounds} rounds. Each is backtested at once,
                and the AI sees only training-period results before the next
                round. The ranking below uses a later period it never saw.
              </p>
              <div className="builder-row">
                <Field
                  label="Optional direction for the AI"
                  className="field-grow"
                >
                  <TextInput
                    small
                    value={guidance}
                    maxLength={500}
                    placeholder="For example: defined-risk ideas for range-bound weeks"
                    onChange={(event) => setGuidance(event.target.value)}
                  />
                </Field>
                <Button
                  variant="primary"
                  disabled={running || !ready || !data.ai.enabled}
                  onClick={() => act(() => api.labGenerate(guidance))}
                >
                  Generate and test
                </Button>
              </div>
            </div>
          </Panel>
        </div>
      </section>
    </>
  );
}

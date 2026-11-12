import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type LabSummary, type TuneNode, type TuneOverview, type TuneSession } from "../api";
import { dateTimeIst, num, signedInr } from "../format";
import { Empty, JobStatus, Panel, Pnl, Select, Status, Tile, type Tone } from "../ui";

const POLL_MS = 3000;
const VERDICTS: Record<string, { tone: Tone; label: string }> = {
  held_up: { tone: "good", label: "Held up on the final test" },
  unproven: { tone: "warning", label: "Final profit could be luck" },
  fails_at_2x_slippage: { tone: "warning", label: "Fails at 2x slippage" },
  failed: { tone: "critical", label: "Lost on the final test" },
  too_few_trades: { tone: "neutral", label: "Too few trades on the final test" },
};

function Metrics({ label, summary }: { label: string; summary: LabSummary }) {
  return (
    <div className="tune-metrics">
      <span className="muted">{label}</span>
      <span>{summary.trades} trades</span>
      <Pnl value={summary.net_pnl}>{signedInr(summary.net_pnl)}</Pnl>
      <span>Sharpe {num(summary.sharpe, 2)}</span>
    </div>
  );
}

interface NodeActions {
  busy: boolean;
  saved: Record<string, boolean>;
  onTune: (node: TuneNode) => void;
  onSave: (node: TuneNode) => void;
}

function NodeCard({
  node,
  best,
  analysis,
  actions,
}: {
  node: TuneNode;
  best: boolean;
  analysis: TuneSession["rounds"];
  actions: NodeActions;
}) {
  const state =
    node.depth === 0
      ? { tone: "neutral" as Tone, label: "Start" }
      : node.accepted
        ? { tone: "good" as Tone, label: "Kept" }
        : { tone: "critical" as Tone, label: "Not kept" };
  const canGrow = node.depth === 0 || node.accepted;
  return (
    <div className={`tune-card${best ? " best" : ""}${node.accepted === false ? " rejected" : ""}`}>
      <div className="tune-card-head">
        <Status tone={state.tone}>{state.label}</Status>
        {best && <Status tone="good">Best version</Status>}
        <strong className="tune-label">{node.label}</strong>
      </div>
      {node.reason && <p className="tune-reason">Why try it: {node.reason}</p>}
      <Metrics label="Design" summary={node.design} />
      <Metrics label="Unseen" summary={node.validate} />
      {node.depth > 0 && (
        <p className="secondary tune-verdict">
          {node.verdict}
          {node.prob_better !== null &&
            node.validate.trades > 0 &&
            `, ${Math.round(node.prob_better * 100)}% chance it is better`}
        </p>
      )}
      <div className="tune-actions">
        {canGrow && (
          <button type="button" className="link-button" disabled={actions.busy} onClick={() => actions.onTune(node)}>
            Tune from here
          </button>
        )}
        {actions.saved[node.id] ? (
          <a className="link-button" href={`#lab/view/${encodeURIComponent(node.id)}`}>
            Open in the lab
          </a>
        ) : (
          <button type="button" className="link-button" onClick={() => actions.onSave(node)}>
            Save as strategy
          </button>
        )}
      </div>
      {analysis.map((round) => (
        <div className="tune-analysis" key={round.round}>
          <strong>
            Round {round.round}, {round.source === "ai" ? "AI analysis" : "built-in changes"}
          </strong>
          <span className="muted">
            {" "}
            · {round.tried.length} tested, {round.kept.length} kept
          </span>
          <p>{round.analysis}</p>
          {round.skipped.length > 0 && (
            <details>
              <summary>{round.skipped.length} suggestions not tested</summary>
              <ul>
                {round.skipped.map((text, i) => (
                  <li key={i}>{text}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}

function Branch({
  node,
  session,
  children,
  actions,
}: {
  node: TuneNode;
  session: TuneSession;
  children: Map<string, TuneNode[]>;
  actions: NodeActions;
}) {
  const below = children.get(node.id) ?? [];
  const kept = below.filter((n) => n.accepted);
  const dropped = below.filter((n) => !n.accepted);
  const analysis = session.rounds.filter((round) => round.parent_id === node.id);
  return (
    <li>
      <NodeCard node={node} best={node.id === session.best_id} analysis={analysis} actions={actions} />
      {below.length > 0 && (
        <ul className="tune-tree">
          {kept.map((child) => (
            <Branch key={child.id} node={child} session={session} children={children} actions={actions} />
          ))}
          {dropped.length > 0 && (
            <li>
              <details className="tune-dropped">
                <summary>
                  {dropped.length} change{dropped.length > 1 ? "s" : ""} not kept
                </summary>
                <ul className="tune-tree">
                  {dropped.map((child) => (
                    <Branch key={child.id} node={child} session={session} children={children} actions={actions} />
                  ))}
                </ul>
              </details>
            </li>
          )}
        </ul>
      )}
    </li>
  );
}

function bestPath(session: TuneSession): TuneNode[] {
  const path: TuneNode[] = [];
  let node: TuneNode | undefined = session.nodes[session.best_id];
  while (node) {
    path.unshift(node);
    node = node.parent_id ? session.nodes[node.parent_id] : undefined;
  }
  return path;
}

function SessionView({ session, actions }: { session: TuneSession; actions: NodeActions }) {
  const children = useMemo(() => {
    const map = new Map<string, TuneNode[]>();
    for (const node of Object.values(session.nodes)) {
      if (!node.parent_id) continue;
      map.set(node.parent_id, [...(map.get(node.parent_id) ?? []), node]);
    }
    for (const list of map.values()) {
      list.sort((a, b) => (b.validate.sharpe ?? -99) - (a.validate.sharpe ?? -99));
    }
    return map;
  }, [session]);
  const root = session.nodes[session.root_id];
  const best = session.nodes[session.best_id];
  const tested = Object.keys(session.nodes).length - 1;
  const kept = Object.values(session.nodes).filter((n) => n.accepted).length;
  const final = session.final;
  const verdict = final ? VERDICTS[final.verdict] : null;
  const path = bestPath(session);
  return (
    <>
      <Panel
        title={`Tuning ${session.root_name}`}
        aside={<span className="muted">Updated {dateTimeIst(session.updated_at ?? session.created_at)}</span>}
      >
        <div className="tiles tune-tiles">
          <Tile label="Changes tested" value={tested} hint={`${kept} kept`} />
          <Tile
            label="Unseen Sharpe"
            value={`${num(root.validate.sharpe, 2)} → ${num(best.validate.sharpe, 2)}`}
            hint="Start → best version"
          />
          <Tile
            label="Unseen net P&L"
            value={
              <>
                <Pnl value={root.validate.net_pnl}>{signedInr(root.validate.net_pnl)}</Pnl>
                {" → "}
                <Pnl value={best.validate.net_pnl}>{signedInr(best.validate.net_pnl)}</Pnl>
              </>
            }
          />
          <Tile label="Unseen trades" value={`${root.validate.trades} → ${best.validate.trades}`} />
        </div>
        <div className="ladder">
          {path.length > 1 && (
            <ol className="ladder-path">
              {path.map((node) => (
                <li key={node.id}>
                  <strong>{node.depth === 0 ? "Start" : node.label}</strong>
                  <small className="secondary">
                    Unseen Sharpe {num(node.validate.sharpe, 2)}, {node.validate.trades} trades
                  </small>
                </li>
              ))}
            </ol>
          )}
          {verdict && final ? (
            <div className="ladder-final">
              <Status tone={verdict.tone}>{verdict.label}</Status>
              <span>
                Final test: {final.test.trades} trades,{" "}
                <Pnl value={final.test.net_pnl}>{signedInr(final.test.net_pnl)}</Pnl>
                {", at 2x slippage "}
                <Pnl value={final.test_2x.net_pnl ?? 0}>{signedInr(final.test_2x.net_pnl)}</Pnl>
              </span>
              {session.root_final && (
                <span className="secondary">
                  Start version on the same period: {session.root_final.test.trades} trades,{" "}
                  <Pnl value={session.root_final.test.net_pnl}>{signedInr(session.root_final.test.net_pnl)}</Pnl>
                </span>
              )}
              <a className="link-button" href={`#lab/backtest/${encodeURIComponent(final.id)}`}>
                Full backtest
              </a>
            </div>
          ) : (
            <p className="secondary">
              No change has been kept yet, so the final test period is still unread. Tune from the start
              again with other settings or guidance.
            </p>
          )}
          {(session.final_period_uses ?? 0) > 1 && (
            <p className="secondary">
              The final test period has been used {session.final_period_uses} times across the lab, so each
              new result there counts for less.
            </p>
          )}
        </div>
      </Panel>
      <Panel title="Tree of changes" aside={<span className="muted">One setting changed per step</span>}>
        <ul className="tune-tree tune-root">
          <Branch node={root} session={session} children={children} actions={actions} />
        </ul>
      </Panel>
    </>
  );
}

export function TunerView({ sessionId, startId }: { sessionId: string | null; startId: string | null }) {
  const [overview, setOverview] = useState<TuneOverview | null>(null);
  const [session, setSession] = useState<TuneSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [start, setStart] = useState(startId ?? "");
  const [rounds, setRounds] = useState(3);
  const [perRound, setPerRound] = useState(4);
  const [useAi, setUseAi] = useState(true);
  const [guidance, setGuidance] = useState("");
  const [saved, setSaved] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    try {
      const next = await api.labTuning();
      setOverview(next);
      if (sessionId) setSession(await api.labTuningSession(sessionId));
      setError(null);
      return next;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the tuner.");
      return null;
    }
  }, [sessionId]);

  useEffect(() => {
    setSession(null);
    void load();
  }, [load]);

  const job = overview?.job ?? null;
  const running = job?.state === "running";
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(async () => {
      const next = await load();
      const finished = next?.job && next.job.state !== "running";
      if (finished && next.job?.kind === "tune" && next.job.session_id && next.job.session_id !== sessionId) {
        window.location.hash = `#lab/tune/s/${encodeURIComponent(next.job.session_id)}`;
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, load, sessionId]);

  useEffect(() => {
    if (!start && overview?.strategies.length) {
      const own = overview.strategies.find((s) => s.source !== "template") ?? overview.strategies[0];
      setStart(own.id);
    }
  }, [overview, start]);

  const begin = async (body: { start_id?: string; session_id?: string; node_id?: string }) => {
    setError(null);
    try {
      await api.labTune({ ...body, rounds, per_round: perRound, guidance, use_ai: useAi && !!overview?.ai.enabled });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start tuning.");
    }
  };

  const actions: NodeActions = {
    busy: running,
    saved,
    onTune: (node) => session && void begin({ session_id: session.id, node_id: node.id }),
    onSave: async (node) => {
      if (!session) return;
      try {
        const { id } = await api.labTuneSave(session.id, node.id);
        setSaved((current) => ({ ...current, [node.id]: true, [id]: true }));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not save.");
      }
    },
  };

  if (!overview) {
    return error ? <p className="ai-error">{error}</p> : <Empty title="Loading the tuner" />;
  }
  const aiOn = overview.ai.enabled;
  return (
    <>
      <Panel title="Tune a strategy" aside={aiOn ? <span className="muted">{overview.ai.model}</span> : undefined}>
        <div className="builder">
          <p className="secondary">
            Each round, {aiOn && useAi ? "the AI reads" : "the tuner reads"} the current best version's results on
            the design sessions and suggests changes to one setting each: a stop, a strike, a filter, an indicator
            period. Every change is backtested, and kept only if it does better on later unseen sessions by more
            than luck explains. The AI never sees those unseen results, only whether a change was kept. The best
            version then gets one final test on a period nothing has read.
          </p>
          <div className="builder-row">
            <Select
              label="Strategy"
              value={start}
              onChange={setStart}
              options={overview.strategies.map((s) => ({
                value: s.id,
                label: `${s.name}${s.source === "template" ? " (template)" : ""}`,
              }))}
            />
            <label className="field">
              Rounds
              <input type="number" min={1} max={8} value={rounds} onChange={(e) => setRounds(Number(e.target.value) || 1)} />
            </label>
            <label className="field">
              Changes per round
              <input
                type="number"
                min={1}
                max={10}
                value={perRound}
                onChange={(e) => setPerRound(Number(e.target.value) || 1)}
              />
            </label>
            <span className="checks">
              <label>
                <input type="checkbox" checked={aiOn && useAi} disabled={!aiOn} onChange={(e) => setUseAi(e.target.checked)} />
                Use the AI model
              </label>
            </span>
          </div>
          <label className="field grow">
            Guidance for the AI (optional)
            <input
              type="text"
              maxLength={500}
              value={guidance}
              placeholder="For example: keep the loss per trade small; try trend filters first"
              onChange={(e) => setGuidance(e.target.value)}
            />
          </label>
          {!aiOn && (
            <p className="secondary">The AI model is off in config/ai.yaml, so built-in changes are used.</p>
          )}
          <div className="builder-row">
            <button
              type="button"
              className="button"
              disabled={running || !overview.ready || !start}
              onClick={() => void begin({ start_id: start })}
            >
              Start tuning
            </button>
            {!overview.ready && (
              <span className="secondary">Tuning needs more stored history, the same as the ladder.</span>
            )}
          </div>
        </div>
        {job && job.kind === "tune" && (
          <div className="panel-job">
            <JobStatus job={job} />
          </div>
        )}
        {job && job.kind !== "tune" && running && (
          <p className="secondary">Another lab job is running: {job.message}</p>
        )}
        {error && <p className="ai-error">{error}</p>}
      </Panel>

      {session && <SessionView session={session} actions={actions} />}

      <Panel title="Tuning sessions">
        {overview.sessions.length === 0 ? (
          <Empty title="No tuning yet">Choose a strategy above and start tuning.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table compact-table">
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Started</th>
                  <th>Tested</th>
                  <th>Kept</th>
                  <th>Unseen Sharpe</th>
                  <th>Final test</th>
                </tr>
              </thead>
              <tbody>
                {overview.sessions.map((row) => (
                  <tr key={row.id} className={row.id === sessionId ? "selected" : undefined}>
                    <td>
                      <a className="link-button" href={`#lab/tune/s/${encodeURIComponent(row.id)}`}>
                        {row.root_name}
                      </a>
                    </td>
                    <td>{dateTimeIst(row.created_at)}</td>
                    <td>{row.tested}</td>
                    <td>{row.kept}</td>
                    <td>
                      {num(row.root_validate?.sharpe, 2)} → {num(row.best_validate?.sharpe, 2)}
                    </td>
                    <td>{row.final ? VERDICTS[row.final.verdict]?.label ?? row.final.verdict : "Not yet"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}

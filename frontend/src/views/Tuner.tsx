import { useCallback, useEffect, useState } from "react";
import {
  api,
  type LabSummary,
  type TechCatalog,
  type TuneNode,
  type TuneOverview,
  type TuneParam,
  type TuneSession,
} from "../api";
import { SharpeShift, TuneCompare } from "./TuneCompare";
import { TuneCharts } from "./TuneCharts";
import { TuneParams } from "./TuneParams";
import { settingName } from "./tuneSettings";
import { TuneSummary } from "./TuneSummary";
import { TuneTiming } from "./TuneTiming";
import { dateTimeIst, num, signedInr } from "../format";
import {
  Empty,
  JobStatus,
  Panel,
  Pnl,
  LabeledSelect,
  Status,
  Tile,
  type Tone,
  Alert,
  Button,
  ButtonLink,
  Field,
  Switch,
  Table,
  TextInput,
  Tiles,
} from "../components/ui";

const POLL_MS = 3000;
const MAX_ROUNDS = 15;
const MAX_PER_ROUND = 10;

function clamp(value: number, low: number, high: number): number {
  return Number.isFinite(value)
    ? Math.min(high, Math.max(low, Math.round(value)))
    : low;
}
const VERDICTS: Record<string, { tone: Tone; label: string }> = {
  held_up: { tone: "good", label: "Held up on the final test" },
  unproven: { tone: "warning", label: "Final profit could be luck" },
  fails_at_2x_slippage: { tone: "warning", label: "Fails at 2x slippage" },
  failed: { tone: "critical", label: "Lost on the final test" },
  too_few_trades: {
    tone: "neutral",
    label: "Too few trades on the final test",
  },
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
  selectedId: string | null;
  /** Fold changes that were not kept under one line per branch. */
  hideDropped: boolean;
  onSelect: (node: TuneNode) => void;
  saved: Record<string, boolean>;
  onTune: (node: TuneNode) => void;
  /** Grow the session from a version, changing only these settings. */
  onTuneWith: (node: TuneNode, params: string[]) => void;
  onSave: (node: TuneNode) => void;
}

function NodeCard({
  node,
  parent,
  best,
  actions,
}: {
  node: TuneNode;
  parent: TuneNode | undefined;
  best: boolean;
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
    <div
      className={`tune-card${best ? " best" : ""}${node.accepted === false ? " rejected" : ""}${
        actions.selectedId === node.id ? " selected" : ""
      }`}
    >
      <div className="tune-card-head">
        <Status tone={state.tone}>{state.label}</Status>
        {best && <Status tone="good">Best version</Status>}
        <strong className="tune-label">{node.label}</strong>
      </div>
      <SharpeShift node={node} parent={parent} />
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
        <Button
          variant="link"
          aria-pressed={actions.selectedId === node.id}
          onClick={() => actions.onSelect(node)}
        >
          {node.depth === 0 ? "Show results" : "See what changed"}
        </Button>
        {canGrow && (
          <Button
            variant="link"
            disabled={actions.busy}
            onClick={() => actions.onTune(node)}
          >
            Tune from here
          </Button>
        )}
        {actions.saved[node.id] ? (
          <ButtonLink
            variant="link"
            href={`#lab/view/${encodeURIComponent(node.id)}`}
          >
            Open in the lab
          </ButtonLink>
        ) : (
          <Button variant="link" onClick={() => actions.onSave(node)}>
            Save as strategy
          </Button>
        )}
      </div>
    </div>
  );
}

type TuneRound = TuneSession["rounds"][number];

/** Kept changes first, then the rest, best unseen Sharpe first within each. */
function byResult(nodes: TuneNode[]): TuneNode[] {
  const sharpe = (n: TuneNode) => n.validate.sharpe ?? -99;
  return [...nodes].sort(
    (a, b) =>
      Number(!!b.accepted) - Number(!!a.accepted) || sharpe(b) - sharpe(a),
  );
}

/** One version, then each round run from it as a branch holding that round's changes. */
function Branch({
  node,
  session,
  actions,
}: {
  node: TuneNode;
  session: TuneSession;
  actions: NodeActions;
}) {
  const rounds = session.rounds.filter((round) => round.parent_id === node.id);
  const inRounds = new Set(rounds.flatMap((round) => round.tried));
  // Sessions saved before rounds listed their changes.
  const loose = Object.values(session.nodes).filter(
    (n) => n.parent_id === node.id && !inRounds.has(n.id),
  );
  return (
    <li>
      <NodeCard
        node={node}
        parent={node.parent_id ? session.nodes[node.parent_id] : undefined}
        best={node.id === session.best_id}
        actions={actions}
      />
      {(rounds.length > 0 || loose.length > 0) && (
        <ul className="tune-tree">
          {rounds.map((round) => (
            <RoundBranch
              key={round.round}
              round={round}
              session={session}
              actions={actions}
            />
          ))}
          {loose.length > 0 && (
            <Changes nodes={loose} session={session} actions={actions} />
          )}
        </ul>
      )}
    </li>
  );
}

function RoundBranch({
  round,
  session,
  actions,
}: {
  round: TuneRound;
  session: TuneSession;
  actions: NodeActions;
}) {
  const nodes = round.tried
    .map((id) => session.nodes[id])
    .filter((n): n is TuneNode => !!n);
  const added = round.built_in_added ?? 0;
  return (
    <li className="tune-round">
      <details className="tune-round-box" open>
        <summary>
          <strong>Round {round.round}</strong>
          <span className="muted">
            {" "}
            · {nodes.length} tested, {round.kept.length} kept ·{" "}
            {round.source === "ai" ? "AI suggestions" : "built-in changes"}
            {added > 0 && round.source === "ai" && `, ${added} built-in`}
          </span>
        </summary>
        <p className="tune-round-analysis">{round.analysis}</p>
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
      </details>
      {nodes.length > 0 && (
        <ul className="tune-tree">
          <Changes nodes={nodes} session={session} actions={actions} />
        </ul>
      )}
    </li>
  );
}

/** A round's changes: kept ones grow their own branches; the others can fold away. */
function Changes({
  nodes,
  session,
  actions,
}: {
  nodes: TuneNode[];
  session: TuneSession;
  actions: NodeActions;
}) {
  const sorted = byResult(nodes);
  const kept = sorted.filter((n) => n.accepted);
  const dropped = sorted.filter((n) => !n.accepted);
  const branch = (child: TuneNode) => (
    <Branch key={child.id} node={child} session={session} actions={actions} />
  );
  return (
    <>
      {kept.map(branch)}
      {actions.hideDropped && dropped.length > 0 ? (
        <li>
          <details className="tune-dropped">
            <summary>
              {dropped.length} change{dropped.length > 1 ? "s" : ""} not kept
            </summary>
            <ul className="tune-tree">{dropped.map(branch)}</ul>
          </details>
        </li>
      ) : (
        dropped.map(branch)
      )}
    </>
  );
}

/** Every try in the order it ran, numbered, with its results beside the version it came from. */
function AllTests({
  session,
  selectedId,
  onSelect,
}: {
  session: TuneSession;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const order = session.rounds.flatMap((round) =>
    round.tried.map((id) => ({ id, round: round.round })),
  );
  const number = new Map(order.map((row, index) => [row.id, index + 1]));
  const from = (node: TuneNode) => {
    if (!node.parent_id || node.parent_id === session.root_id) return "Start";
    return `Try ${number.get(node.parent_id) ?? "?"}`;
  };
  if (!order.length) {
    return (
      <Panel title="All tests">
        <Empty title="Nothing tested yet">
          The starting version is tested first, then each round's changes.
        </Empty>
      </Panel>
    );
  }
  return (
    <Panel
      title="All tests"
      aside={
        <span className="muted">
          {order.length} tries in {session.rounds.length} round
          {session.rounds.length === 1 ? "" : "s"}. Choose a row to compare.
        </span>
      }
    >
      <Table compact className="table-ideas tune-all">
        <thead>
          <tr>
            <th>#</th>
            <th>Round</th>
            <th>Change</th>
            <th>From</th>
            <th className="right">Design trades</th>
            <th className="right">Design P&amp;L</th>
            <th className="right">Unseen trades</th>
            <th className="right">Unseen P&amp;L</th>
            <th className="right">Unseen Sharpe</th>
            <th>Result</th>
          </tr>
        </thead>
        <tbody>
          {order.map(({ id, round }, index) => {
            const node = session.nodes[id];
            if (!node) return null;
            const parent = node.parent_id
              ? session.nodes[node.parent_id]
              : undefined;
            const before = parent?.validate.sharpe;
            const after = node.validate.sharpe;
            const delta =
              before !== null &&
              before !== undefined &&
              after !== null &&
              after !== undefined
                ? after - before
                : null;
            return (
              <tr
                key={id}
                className={
                  id === selectedId
                    ? "selected"
                    : node.accepted
                      ? ""
                      : "tune-row-dropped"
                }
                tabIndex={0}
                onClick={() => onSelect(id)}
                onKeyDown={(event) => event.key === "Enter" && onSelect(id)}
              >
                <td>{index + 1}</td>
                <td>{round}</td>
                <td className="wrap-cell">
                  <span className="tune-label">{node.label}</span>
                  {node.id === session.best_id && (
                    <span className="badge-chip tune-best-chip">Best</span>
                  )}
                </td>
                <td className="secondary">{from(node)}</td>
                <td className="right">{node.design.trades}</td>
                <td className="right">
                  <Pnl value={node.design.net_pnl}>
                    {signedInr(node.design.net_pnl)}
                  </Pnl>
                </td>
                <td className="right">{node.validate.trades}</td>
                <td className="right">
                  <Pnl value={node.validate.net_pnl}>
                    {signedInr(node.validate.net_pnl)}
                  </Pnl>
                </td>
                <td className="right">
                  {num(after, 2)}
                  {delta !== null && (
                    <small
                      className={
                        delta > 0 ? "pnl-up" : delta < 0 ? "pnl-down" : "muted"
                      }
                    >
                      {" "}
                      ({delta > 0 ? "+" : ""}
                      {num(delta, 2)})
                    </small>
                  )}
                </td>
                <td className="wrap-cell">
                  <Status tone={node.accepted ? "good" : "critical"}>
                    {node.accepted ? "Kept" : "Not kept"}
                  </Status>
                  <div className="secondary">{node.verdict}</div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </Table>
    </Panel>
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

function SessionView({
  session,
  actions: outer,
  catalog,
}: {
  session: TuneSession;
  actions: Omit<NodeActions, "selectedId" | "onSelect" | "hideDropped">;
  catalog: TechCatalog | null;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Charts carry the overview, so the tree starts with changes not kept folded away.
  const [hideDropped, setHideDropped] = useState(true);
  const [labels, setLabels] = useState<Record<string, string>>({});
  useEffect(() => {
    let current = true;
    api
      .labTuneParams(session.root_id)
      .then(({ params }) => {
        if (current)
          setLabels(
            Object.fromEntries(params.map((p) => [p.path, p.full ?? p.label])),
          );
      })
      // A deleted start strategy leaves the paths themselves as names.
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [session.root_id]);
  const actions: NodeActions = {
    ...outer,
    selectedId,
    hideDropped,
    onSelect: (node) => setSelectedId(node.id),
  };
  const selected = selectedId ? session.nodes[selectedId] : undefined;
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
        aside={
          <span className="muted">
            Updated {dateTimeIst(session.updated_at ?? session.created_at)}
          </span>
        }
      >
        <Tiles className="tune-tiles">
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
                <Pnl value={root.validate.net_pnl}>
                  {signedInr(root.validate.net_pnl)}
                </Pnl>
                {" → "}
                <Pnl value={best.validate.net_pnl}>
                  {signedInr(best.validate.net_pnl)}
                </Pnl>
              </>
            }
          />
          <Tile
            label="Unseen trades"
            value={`${root.validate.trades} → ${best.validate.trades}`}
          />
        </Tiles>
        <div className="ladder">
          {path.length > 1 && (
            <ol className="ladder-path">
              {path.map((node) => (
                <li key={node.id}>
                  <strong>{node.depth === 0 ? "Start" : node.label}</strong>
                  <small className="secondary">
                    Unseen Sharpe {num(node.validate.sharpe, 2)},{" "}
                    {node.validate.trades} trades
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
                <Pnl value={final.test.net_pnl}>
                  {signedInr(final.test.net_pnl)}
                </Pnl>
                {", at 2x slippage "}
                <Pnl value={final.test_2x.net_pnl ?? 0}>
                  {signedInr(final.test_2x.net_pnl)}
                </Pnl>
              </span>
              {session.root_final && (
                <span className="secondary">
                  Start version on the same period:{" "}
                  {session.root_final.test.trades} trades,{" "}
                  <Pnl value={session.root_final.test.net_pnl}>
                    {signedInr(session.root_final.test.net_pnl)}
                  </Pnl>
                </span>
              )}
              <ButtonLink
                variant="link"
                href={`#lab/backtest/${encodeURIComponent(final.id)}`}
              >
                Full backtest
              </ButtonLink>
            </div>
          ) : (
            <p className="secondary">
              No change has been kept yet, so the final test period is still
              unread. Tune from the start again with other settings or guidance.
            </p>
          )}
          {(session.final_period_uses ?? 0) > 1 && (
            <p className="secondary">
              The final test period has been used {session.final_period_uses}{" "}
              times across the lab, so each new result there counts for less.
            </p>
          )}
        </div>
      </Panel>
      <div className={selected ? "split tune-split" : ""}>
        <div className="tune-main">
          <Panel title="Results">
            <TuneSummary
              key={`${session.id}-${session.updated_at ?? ""}`}
              session={session}
              labels={labels}
              busy={actions.busy}
              selectedId={selectedId}
              onSelect={(node) => setSelectedId(node.id)}
              onTuneAgain={(params) =>
                actions.onTuneWith(session.nodes[session.best_id], params)
              }
            />
            <TuneTiming session={session} selectedId={selectedId} />
            <TuneCharts
              session={session}
              labels={labels}
              selectedId={selectedId}
              onSelect={(node) => setSelectedId(node.id)}
            />
          </Panel>
          <Panel
            title="Tree of changes"
            aside={
              <Button
                variant="link"
                aria-pressed={hideDropped}
                onClick={() => setHideDropped((value) => !value)}
              >
                {hideDropped
                  ? "Show changes not kept"
                  : "Hide changes not kept"}
              </Button>
            }
          >
            {session.params && session.params.length > 0 && (
              <p className="panel-note tune-tree-note">
                Settings tuned:{" "}
                {session.params.map((p) => settingName(p, labels)).join(", ")}
              </p>
            )}
            <p className="panel-note tune-tree-note">
              Each round branches from the version it tuned and holds exactly
              the changes it tested, one setting each. A kept change starts the
              next round’s branch. Choose “See what changed” on any try to
              compare it with the version it came from.
            </p>
            <ul className="tune-tree tune-root">
              <Branch node={root} session={session} actions={actions} />
            </ul>
          </Panel>
          <AllTests
            session={session}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        </div>
        {selected && (
          <aside className="panel detail-panel">
            <TuneCompare
              node={selected}
              parent={
                selected.parent_id
                  ? session.nodes[selected.parent_id]
                  : undefined
              }
              catalog={catalog}
              number={
                session.rounds
                  .flatMap((round) => round.tried)
                  .indexOf(selected.id) + 1 || undefined
              }
              onClose={() => setSelectedId(null)}
            />
          </aside>
        )}
      </div>
    </>
  );
}

export function TunerView({
  sessionId,
  startId,
}: {
  sessionId: string | null;
  startId: string | null;
}) {
  const [overview, setOverview] = useState<TuneOverview | null>(null);
  const [session, setSession] = useState<TuneSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [start, setStart] = useState(startId ?? "");
  const [rounds, setRounds] = useState(3);
  const [perRound, setPerRound] = useState(4);
  const [useAi, setUseAi] = useState(true);
  const [guidance, setGuidance] = useState("");
  const [saved, setSaved] = useState<Record<string, boolean>>({});
  const [params, setParams] = useState<TuneParam[]>([]);
  const [chosen, setChosen] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    try {
      const next = await api.labTuning();
      setOverview(next);
      if (sessionId) setSession(await api.labTuningSession(sessionId));
      setError(null);
      return next;
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not load the tuner.",
      );
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
      if (
        finished &&
        next.job?.kind === "tune" &&
        next.job.session_id &&
        next.job.session_id !== sessionId
      ) {
        window.location.hash = `#tune/s/${encodeURIComponent(next.job.session_id)}`;
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, load, sessionId]);

  useEffect(() => {
    if (!start && overview?.strategies.length) {
      const own =
        overview.strategies.find((s) => s.source !== "template") ??
        overview.strategies[0];
      setStart(own.id);
    }
  }, [overview, start]);

  useEffect(() => {
    if (!start) return;
    let current = true;
    api
      .labTuneParams(start)
      .then(({ params: found }) => {
        if (!current) return;
        setParams(found);
        setChosen(new Set(found.map((p) => p.path)));
      })
      .catch(() => current && setParams([]));
    return () => {
      current = false;
    };
  }, [start]);

  const begin = async (body: {
    start_id?: string;
    session_id?: string;
    node_id?: string;
    params?: string[] | null;
  }) => {
    setError(null);
    try {
      await api.labTune({
        ...body,
        rounds,
        per_round: perRound,
        guidance,
        use_ai: useAi && !!overview?.ai.enabled,
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start tuning.");
    }
  };

  const actions: Omit<NodeActions, "selectedId" | "onSelect" | "hideDropped"> =
    {
      busy: running,
      saved,
      onTune: (node) =>
        session && void begin({ session_id: session.id, node_id: node.id }),
      onTuneWith: (node, params) =>
        session &&
        void begin({ session_id: session.id, node_id: node.id, params }),
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
    return error ? (
      <Alert tone="critical">{error}</Alert>
    ) : (
      <Empty title="Loading the tuner" />
    );
  }
  const aiOn = overview.ai.enabled;
  return (
    <>
      <Panel
        title="Tune a strategy"
        aside={
          aiOn ? <span className="muted">{overview.ai.model}</span> : undefined
        }
      >
        <div className="builder">
          <p className="secondary">
            Each round, {aiOn && useAi ? "the AI reads" : "the tuner reads"} the
            current best version's results on the design sessions and suggests
            changes to one setting each: a stop, a strike, a filter, an
            indicator period. Every change is backtested, and kept only if it
            does better on later unseen sessions by more than luck explains. The
            AI never sees those unseen results, only whether a change was kept.
            The best version then gets one final test on a period nothing has
            read.
          </p>
          <div className="builder-row">
            <LabeledSelect
              stacked
              label="Strategy"
              value={start}
              onChange={setStart}
              options={overview.strategies.map((s) => ({
                value: s.id,
                label: `${s.name}${s.source === "template" ? " (template)" : ""}`,
              }))}
            />
            <Field label={`Rounds, 1 to ${MAX_ROUNDS}`}>
              <TextInput
                small
                narrow
                type="number"
                min={1}
                max={MAX_ROUNDS}
                value={rounds}
                onChange={(e) =>
                  setRounds(clamp(Number(e.target.value), 1, MAX_ROUNDS))
                }
              />
            </Field>
            <Field label={`Changes per round, 1 to ${MAX_PER_ROUND}`}>
              <TextInput
                small
                narrow
                type="number"
                min={1}
                max={MAX_PER_ROUND}
                value={perRound}
                onChange={(e) =>
                  setPerRound(clamp(Number(e.target.value), 1, MAX_PER_ROUND))
                }
              />
            </Field>
            <Switch
              id="tune-use-ai"
              checked={aiOn && useAi}
              disabled={!aiOn}
              onChange={setUseAi}
            >
              Use the AI model
            </Switch>
          </div>
          <Field label="Guidance for the AI (optional)" className="field-grow">
            <TextInput
              small
              maxLength={500}
              value={guidance}
              placeholder="For example: keep the loss per trade small; try trend filters first"
              onChange={(e) => setGuidance(e.target.value)}
            />
          </Field>
          {!aiOn && (
            <p className="secondary">
              The AI model is off in config/ai.yaml, so built-in changes are
              used.
            </p>
          )}
          {params.length > 0 && (
            <TuneParams
              params={params}
              chosen={chosen}
              aiOn={aiOn && useAi}
              onChange={setChosen}
            />
          )}
          <div className="builder-row">
            <Button
              variant="primary"
              disabled={
                running ||
                !overview.ready ||
                !start ||
                (params.length > 0 && chosen.size === 0)
              }
              onClick={() =>
                void begin({
                  start_id: start,
                  // Every setting ticked tunes them all, including ones added later.
                  params: chosen.size === params.length ? null : [...chosen],
                })
              }
            >
              Start tuning
            </Button>
            {!overview.ready && (
              <span className="secondary">
                Tuning needs more stored history, the same as the ladder.
              </span>
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
        {error && <Alert tone="critical">{error}</Alert>}
      </Panel>

      {session && (
        <SessionView
          session={session}
          actions={actions}
          catalog={overview.technical ?? null}
        />
      )}

      <Panel title="Tuning sessions">
        {overview.sessions.length === 0 ? (
          <Empty title="No tuning yet">
            Choose a strategy above and start tuning.
          </Empty>
        ) : (
          <Table compact>
            <thead>
              <tr>
                <th>Strategy</th>
                <th>Started</th>
                <th className="right">Tested</th>
                <th className="right">Kept</th>
                <th className="right">Unseen Sharpe</th>
                <th>Final test</th>
              </tr>
            </thead>
            <tbody>
              {overview.sessions.map((row) => (
                <tr
                  key={row.id}
                  className={row.id === sessionId ? "selected" : undefined}
                >
                  <td>
                    <ButtonLink
                      variant="link"
                      href={`#tune/s/${encodeURIComponent(row.id)}`}
                    >
                      {row.root_name}
                    </ButtonLink>
                  </td>
                  <td>{dateTimeIst(row.created_at)}</td>
                  <td className="right">{row.tested}</td>
                  <td className="right">{row.kept}</td>
                  <td className="right">
                    {num(row.root_validate?.sharpe, 2)} →{" "}
                    {num(row.best_validate?.sharpe, 2)}
                  </td>
                  <td>
                    {row.final
                      ? (VERDICTS[row.final.verdict]?.label ??
                        row.final.verdict)
                      : "Not yet"}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Panel>
    </>
  );
}

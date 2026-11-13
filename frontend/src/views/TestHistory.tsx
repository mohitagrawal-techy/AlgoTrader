import { useCallback, useEffect, useState } from "react";
import { api, type HistoryEntry, type HistoryPeriod, type HistoryRun, type LabSummary, type TestHistory } from "../api";
import { dateTimeIst, fraction, num, shortDate, signedInr } from "../format";
import { Empty, Panel, Pnl, Segmented, Status, Tile, type Tone } from "../ui";

const VERDICTS: Record<string, { tone: Tone; label: string }> = {
  held_up: { tone: "good", label: "Held up on unseen data" },
  unproven: { tone: "warning", label: "Profit could be luck" },
  fails_at_2x_slippage: { tone: "warning", label: "Fails at 2x slippage" },
  failed: { tone: "critical", label: "Lost on unseen data" },
  too_few_trades: { tone: "neutral", label: "Too few trades" },
};
const PAGE = 100;

function entryFromHash(): string | null {
  const [first, second] = window.location.hash.slice(1).split("/");
  return first === "history" && second ? decodeURIComponent(second) : null;
}

function PeriodCell({ period }: { period: HistoryPeriod | undefined }) {
  if (!period) return <span className="muted">–</span>;
  return (
    <>
      <div>
        <Pnl value={period.net_pnl}>{signedInr(period.net_pnl)}</Pnl>
        <span className="muted">, {num(period.trades)} trades</span>
      </div>
      <div className="secondary">
        {period.folds > 1 ? `${period.folds} folds, ` : ""}
        {period.sharpe !== null ? `Sharpe ${num(period.sharpe, 2)}, ` : ""}
        {shortDate(period.start)} – {shortDate(period.end)}
      </div>
    </>
  );
}

function Row({ run }: { run: HistoryRun }) {
  const training = run.periods.find((p) => p.label === "Training");
  const unseen = run.periods.find((p) => p.label !== "Training");
  const verdict = run.verdict ? VERDICTS[run.verdict] : null;
  return (
    <tr>
      <td className="nowrap">{dateTimeIst(run.ts)}</td>
      <td>{run.kind_label}</td>
      <td className="wrap-cell">
        <div className="primary">
          {run.kind === "lab" && run.exists && !run.changed_since && run.strategy_id ? (
            <a href={`#lab/backtest/${encodeURIComponent(run.strategy_id)}`}>{run.strategy_name}</a>
          ) : (
            run.strategy_name
          )}
        </div>
        {run.step && <div className="secondary">Changed {run.step}</div>}
        {run.settings_tried ? <div className="secondary">{run.settings_tried} settings tried on training folds</div> : null}
      </td>
      <td>
        <PeriodCell period={training} />
      </td>
      <td>
        <PeriodCell period={unseen} />
      </td>
      <td className="wrap-cell">
        {verdict && <Status tone={verdict.tone}>{verdict.label}</Status>}
        {run.kind === "lab" && !run.exists && <div className="secondary">Strategy since deleted</div>}
        {run.changed_since && <div className="secondary">Rules changed since</div>}
      </td>
      <td className="right">
        {run.entry_id && (
          <a className="button ghost small" href={`#history/${encodeURIComponent(run.entry_id)}`}>
            Open
          </a>
        )}
      </td>
    </tr>
  );
}

function Stats({ title, stats }: { title: string; stats: LabSummary | undefined }) {
  if (!stats) return null;
  return (
    <Tile
      label={title}
      value={<Pnl value={stats.net_pnl}>{signedInr(stats.net_pnl)}</Pnl>}
      hint={`${num(stats.trades)} trades${stats.win_rate != null ? `, ${fraction(stats.win_rate, 0)} won` : ""}${
        stats.sharpe != null ? `, Sharpe ${num(stats.sharpe, 2)}` : ""
      }`}
    />
  );
}

function EntryView({ id }: { id: string }) {
  const [data, setData] = useState<HistoryEntry | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    api
      .testHistoryEntry(id)
      .then((body) => active && setData(body))
      .catch((reason) => active && setError(reason instanceof Error ? reason.message : "Could not load this test."));
    return () => {
      active = false;
    };
  }, [id]);
  if (error) return <Empty title="Could not load this test">{error}</Empty>;
  if (!data) return <Empty title="Loading…" />;
  const { entry } = data;
  const verdict = VERDICTS[entry.verdict];
  const latest = data.latest ? VERDICTS[data.latest.verdict] : null;
  const windows = entry.windows ?? {};
  return (
    <>
      <div className="strike-page-head">
        <a className="button ghost" href="#history">
          ← Test history
        </a>
        <div>
          <h2 className="strike-page-title">
            {entry.name} {verdict && <Status tone={verdict.tone}>{verdict.label}</Status>}
          </h2>
          <span className="muted">Tested {dateTimeIst(entry.evaluated_at)}</span>
        </div>
      </div>
      <div className="tiles tiles-4">
        <Stats title={`Training, ${shortDate(windows.train_start)} – ${shortDate(windows.train_end)}`} stats={entry.train} />
        <Stats title={`Test (unseen), ${shortDate(windows.test_start)} – ${shortDate(windows.test_end)}`} stats={entry.test} />
        <Tile
          label="Test at 2x slippage"
          value={<Pnl value={entry.test_2x?.net_pnl ?? 0}>{signedInr(entry.test_2x?.net_pnl)}</Pnl>}
          hint={`${num(entry.test_2x?.trades)} trades`}
        />
        <Tile
          label="Luck-adjusted floor"
          value={entry.evidence ? signedInr(entry.evidence.floor) : "–"}
          hint={entry.evidence ? `Judged against ${num(entry.evidence.trials)} strategies tried` : "Not enough trades"}
        />
      </div>
      <Panel title="Rules as tested">
        <div className="history-rules">
          {data.legs_text && (
            <div className="legs">
              {data.legs_text.split(", ").map((leg) => (
                <code key={leg}>{leg}</code>
              ))}
            </div>
          )}
          {data.entry_text && <p>Enter {data.entry_text}.</p>}
          {data.same_rules ? (
            <p className="secondary">The strategy still has exactly these rules.</p>
          ) : data.current ? (
            <p className="secondary">
              The strategy has changed since this test.
              {data.latest && latest && (
                <>
                  {" "}Its latest test, {dateTimeIst(data.latest.evaluated_at)}: <Status tone={latest.tone}>{latest.label}</Status>
                </>
              )}
            </p>
          ) : (
            <p className="secondary">The strategy has been deleted since.</p>
          )}
          {data.current && (
            <div className="history-actions">
              <a className="button ghost" href={`#lab/backtest/${encodeURIComponent(entry.strategy_id)}`}>
                Latest backtest
              </a>
              <a className="button ghost" href={`#lab/view/${encodeURIComponent(entry.strategy_id)}`}>
                Open in the lab
              </a>
            </div>
          )}
        </div>
      </Panel>
    </>
  );
}

export function TestHistoryView() {
  const [entry, setEntry] = useState<string | null>(entryFromHash);
  const [kind, setKind] = useState("all");
  const [query, setQuery] = useState("");
  const [data, setData] = useState<TestHistory | null>(null);
  const [runs, setRuns] = useState<HistoryRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const onHash = () => setEntry(entryFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const load = useCallback(
    async (offset: number) => {
      setLoading(true);
      try {
        const body = await api.testHistory({ kind: kind === "all" ? undefined : kind, q: query, offset, limit: PAGE });
        setData(body);
        setRuns((current) => (offset ? [...current, ...body.runs] : body.runs));
        setError(null);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not load the test history.");
      } finally {
        setLoading(false);
      }
    },
    [kind, query],
  );

  useEffect(() => {
    if (entry) return;
    const timer = window.setTimeout(() => load(0), 200);
    return () => window.clearTimeout(timer);
  }, [load, entry]);

  if (entry) return <EntryView id={entry} />;
  if (error && !data) return <Empty title="Could not load the test history">{error}</Empty>;
  if (!data) return <Empty title="Loading…" />;

  const counts = new Map(data.summary.by_kind.map((k) => [k.kind, k.runs]));
  return (
    <>
      <div className="tiles tiles-4">
        <Tile label="Tests run" value={num(data.summary.runs)} hint={`${num(data.summary.strategies)} strategies`} />
        <Tile label="Lab tests" value={num(counts.get("lab") ?? 0)} hint="Training and unseen test periods" />
        <Tile
          label="Tuner and ladder checks"
          value={num((counts.get("tune") ?? 0) + (counts.get("ladder") ?? 0))}
          hint="One change each, on unseen sessions"
        />
        <Tile
          label="Test period reads"
          value={data.test_period ? num(data.test_period.reads) : "–"}
          hint={
            data.test_period
              ? `${shortDate(data.test_period.start)} – ${shortDate(data.test_period.end)}. Each read makes a pass count for less.`
              : "Not enough history yet"
          }
        />
      </div>

      <div className="toolbar">
        <Segmented
          label="Kind"
          value={kind}
          onChange={setKind}
          options={[
            { value: "all", label: "All", count: data.summary.runs },
            ...data.kinds.map((k) => ({ value: k.value, label: k.label, count: counts.get(k.value) ?? 0 })),
          ]}
        />
        <label className="field history-search">
          <span className="visually-hidden">Search</span>
          <input
            type="search"
            value={query}
            placeholder="Search strategy or change"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <span className="toolbar-note">
          {num(data.total)} shown{loading ? ", updating…" : ""}
        </span>
      </div>

      <Panel
        title="Every test, newest first"
        aside={<span className="muted">Full detail is kept for lab tests run from now on</span>}
      >
        {runs.length === 0 ? (
          <Empty title="No tests match">Try another kind or search.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table compact-table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Kind</th>
                  <th>Strategy</th>
                  <th>Training</th>
                  <th>Unseen</th>
                  <th>Result</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <Row key={`${run.ts}-${run.kind}-${run.strategy_id}-${run.step}`} run={run} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {runs.length < data.total && (
          <div className="history-more">
            <button type="button" className="button ghost" disabled={loading} onClick={() => load(runs.length)}>
              Show {Math.min(PAGE, data.total - runs.length)} more
            </button>
          </div>
        )}
      </Panel>
    </>
  );
}

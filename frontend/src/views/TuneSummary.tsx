import { useMemo, useState } from "react";
import type { TuneNode, TuneSession } from "../api";
import { num, signedInr } from "../format";
import { Button, Status, Table, type Tone } from "../components/ui";
import { settingName, settingValue } from "./tuneSettings";

type Verdict = "kept" | "promising" | "mixed" | "unclear" | "hurt" | "untried";

// How likely a change is better on unseen sessions, from the tuner's own luck test.
const LIKELY = 0.7;
const UNLIKELY = 0.4;

const VERDICT: Record<Verdict, { tone: Tone; label: string; advice: string }> =
  {
    kept: {
      tone: "good",
      label: "Kept",
      advice: "Held up on unseen sessions. Tune again around the new value.",
    },
    promising: {
      tone: "warning",
      label: "Promising",
      advice:
        "Likely better on unseen sessions and no worse on design sessions, but not by enough to keep. Worth another round.",
    },
    mixed: {
      tone: "warning",
      label: "Mixed",
      advice:
        "Better on unseen sessions but worse on design sessions, so the gain may be luck in one period. Tune it only with a reason.",
    },
    unclear: {
      tone: "neutral",
      label: "No clear effect",
      advice:
        "The values tried moved results by about as much as chance does. Keep it as is.",
    },
    hurt: {
      tone: "critical",
      label: "Keep as is",
      advice:
        "Every value tried did worse than the version it came from. Leave it out of the next run.",
    },
    untried: {
      tone: "neutral",
      label: "Not tried",
      advice: "Chosen, but no change to it was tested in this session.",
    },
  };

interface Try {
  node: TuneNode;
  round: number;
  shown: string;
  /** Unseen Sharpe and net P&L against the version the change was made to. */
  dSharpe: number | null;
  dPnl: number | null;
  /** Design-session Sharpe against that version. */
  dDesign: number | null;
  /** The tuner's chance this change is better on unseen sessions. */
  chance: number | null;
}

interface Row {
  path: string;
  start: string;
  tries: Try[];
  best: Try | null;
  verdict: Verdict;
}

function plain(value: number): string {
  if (Number.isInteger(value)) return String(value);
  return num(value, Math.abs(value) < 10 ? 2 : 0);
}

function delta(a: number | null | undefined, b: number | null | undefined) {
  return a === null || a === undefined || b === null || b === undefined
    ? null
    : a - b;
}

/** The value a change set, as text: a number when it has one, else the label's end. */
function shownValue(node: TuneNode, path: string): string {
  const value = settingValue(node, path, "new");
  if (value !== null) return plain(value);
  if (node.label.startsWith("remove ")) return "removed";
  if (node.label.startsWith("add to ")) return "added";
  return node.label.split("→").pop()?.trim() ?? node.label;
}

function startValue(node: TuneNode, path: string): string {
  const value = settingValue(node, path, "old");
  if (value !== null) return plain(value);
  const match = node.label.match(/:\s*(.+?)\s*→/);
  return match ? match[1] : "—";
}

function rows(session: TuneSession, chosen: string[]): Row[] {
  const byPath = new Map<string, Try[]>();
  for (const round of session.rounds) {
    for (const id of round.tried) {
      const node = session.nodes[id];
      if (!node?.path) continue;
      const parent = node.parent_id ? session.nodes[node.parent_id] : undefined;
      const entry: Try = {
        node,
        round: round.round,
        shown: shownValue(node, node.path),
        dSharpe: delta(node.validate.sharpe, parent?.validate.sharpe),
        dPnl: delta(node.validate.net_pnl, parent?.validate.net_pnl),
        dDesign: delta(node.design.sharpe, parent?.design.sharpe),
        chance: node.prob_better,
      };
      byPath.set(node.path, [...(byPath.get(node.path) ?? []), entry]);
    }
  }
  const found: Row[] = [...byPath.entries()].map(([path, tries]) => {
    // The most likely improvement, then the largest; a bigger gain at 56% is weaker evidence
    // than a smaller one at 84%.
    const best = tries.reduce<Try | null>((top, t) => {
      if (t.dSharpe === null) return top;
      if (top === null) return t;
      const a = [(t.chance ?? 0) * Number(t.dSharpe > 0), t.dSharpe];
      const b = [
        (top.chance ?? 0) * Number((top.dSharpe ?? 0) > 0),
        top.dSharpe ?? 0,
      ];
      return a[0] > b[0] || (a[0] === b[0] && a[1] > b[1]) ? t : top;
    }, null);
    const likely = tries.filter(
      (t) => (t.chance ?? 0) >= LIKELY && (t.dSharpe ?? 0) > 0,
    );
    const verdict: Verdict = tries.some((t) => t.node.accepted)
      ? "kept"
      : likely.some((t) => (t.dDesign ?? 0) >= 0)
        ? "promising"
        : likely.length
          ? "mixed"
          : tries.every((t) => (t.chance ?? 0) < UNLIKELY)
            ? "hurt"
            : "unclear";
    return {
      path,
      start: startValue(tries[0].node, path),
      tries,
      best,
      verdict,
    };
  });
  for (const path of chosen) {
    if (!byPath.has(path) && !path.endsWith(".+")) {
      found.push({
        path,
        start: "—",
        tries: [],
        best: null,
        verdict: "untried",
      });
    }
  }
  const order: Verdict[] = [
    "kept",
    "promising",
    "mixed",
    "unclear",
    "untried",
    "hurt",
  ];
  return found.sort(
    (a, b) =>
      order.indexOf(a.verdict) - order.indexOf(b.verdict) ||
      (b.best?.dSharpe ?? -99) - (a.best?.dSharpe ?? -99),
  );
}

/** One row per setting the session changed: what was tried, what helped, and which
 * settings to tune in the next run. */
export function TuneSummary({
  session,
  labels,
  busy,
  selectedId,
  onSelect,
  onTuneAgain,
}: {
  session: TuneSession;
  labels: Record<string, string>;
  busy: boolean;
  selectedId: string | null;
  onSelect: (node: TuneNode) => void;
  /** Start another run from the best version with only these settings. */
  onTuneAgain: (params: string[]) => void;
}) {
  const table = useMemo(() => rows(session, session.params ?? []), [session]);
  const [next, setNext] = useState<Set<string>>(
    () =>
      new Set(
        table
          .filter((r) => r.verdict === "kept" || r.verdict === "promising")
          .map((r) => r.path),
      ),
  );
  if (!table.length) return null;
  const best = session.nodes[session.best_id];
  const toggle = (path: string, on: boolean) => {
    const changed = new Set(next);
    if (on) changed.add(path);
    else changed.delete(path);
    setNext(changed);
  };
  const counts = table.reduce<Record<Verdict, number>>(
    (all, r) => ({ ...all, [r.verdict]: all[r.verdict] + 1 }),
    { kept: 0, promising: 0, mixed: 0, unclear: 0, hurt: 0, untried: 0 },
  );
  return (
    <section className="tune-summary" aria-label="What each setting did">
      <h3 className="tune-chart-title">
        What each setting did
        <small className="secondary">
          {" "}
          {(Object.keys(VERDICT) as Verdict[])
            .filter((v) => counts[v])
            .map((v) => `${counts[v]} ${VERDICT[v].label.toLowerCase()}`)
            .join(", ")}
          . Values show the change in unseen Sharpe against the version each was
          tried on; point at one for more.
        </small>
      </h3>
      <Table compact className="tune-summary-table">
        <thead>
          <tr>
            <th>Tune next</th>
            <th>Setting</th>
            <th>Start</th>
            <th>Values tried</th>
            <th>Best</th>
            <th>Verdict</th>
          </tr>
        </thead>
        <tbody>
          {table.map((row) => {
            const verdict = VERDICT[row.verdict];
            const name = settingName(row.path, labels);
            return (
              <tr key={row.path}>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`Tune ${name} next`}
                    checked={next.has(row.path)}
                    onChange={(event) => toggle(row.path, event.target.checked)}
                  />
                </td>
                <td className="wrap-cell">
                  <strong className="tune-summary-name" title={row.path}>
                    {name}
                  </strong>
                </td>
                <td>{row.start}</td>
                <td className="wrap-cell">
                  <div className="tune-tries">
                    {row.tries.map((t) => {
                      const tone =
                        t.dSharpe === null
                          ? "flat"
                          : t.dSharpe > 0
                            ? "up"
                            : t.dSharpe < 0
                              ? "down"
                              : "flat";
                      return (
                        <button
                          key={t.node.id}
                          type="button"
                          className={`tune-try ${tone}${t.node.accepted ? " kept" : ""}${t.node.id === selectedId ? " selected" : ""}`}
                          title={`Round ${t.round}: ${t.node.label}. Unseen Sharpe ${num(t.node.validate.sharpe, 2)}, net P&L ${signedInr(t.node.validate.net_pnl)}${t.dPnl !== null ? ` (${t.dPnl >= 0 ? "+" : ""}${signedInr(t.dPnl)} against its version)` : ""}.${t.dDesign !== null ? ` Design Sharpe ${t.dDesign >= 0 ? "+" : ""}${num(t.dDesign, 2)} against its version.` : ""}${t.chance !== null ? ` ${Math.round(t.chance * 100)}% likely better on unseen sessions.` : ""} ${t.node.accepted ? "Kept." : "Not kept."}`}
                          onClick={() => onSelect(t.node)}
                        >
                          <span>{t.shown}</span>
                          <small>
                            {t.dSharpe === null
                              ? "—"
                              : `${t.dSharpe > 0 ? "+" : ""}${num(t.dSharpe, 2)}`}
                          </small>
                          {t.node.accepted && <span aria-label="kept">✓</span>}
                        </button>
                      );
                    })}
                  </div>
                </td>
                <td>
                  {row.best ? (
                    <>
                      {row.best.shown}
                      <div className="muted">
                        Round {row.best.round}
                        {row.best.chance !== null &&
                          `, ${Math.round(row.best.chance * 100)}% likely`}
                      </div>
                      {row.best.dDesign !== null && (
                        <div
                          className={
                            row.best.dDesign < 0 ? "pnl-down" : "muted"
                          }
                        >
                          Design {row.best.dDesign >= 0 ? "+" : ""}
                          {num(row.best.dDesign, 2)}
                        </div>
                      )}
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="wrap-cell">
                  <Status tone={verdict.tone}>{verdict.label}</Status>
                  <div className="secondary tune-summary-advice">
                    {verdict.advice}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </Table>
      {!table.some(
        (r) => r.verdict === "kept" || r.verdict === "promising",
      ) && (
        <p className="secondary">
          No setting both held up on unseen sessions and stayed as good on
          design sessions, so this run gives no clear direction. Tune different
          settings, or treat it as a sign the strategy has no edge to tune.
        </p>
      )}
      <div className="tune-summary-actions">
        <Button
          variant="primary"
          disabled={busy || next.size === 0}
          onClick={() => onTuneAgain([...next])}
        >
          Tune again with {next.size} setting{next.size === 1 ? "" : "s"}
        </Button>
        <span className="secondary">
          Starts from the best version
          {best && best.depth > 0 ? ` (${best.label})` : " (the start)"} and
          keeps every other setting as it is there. Rounds and changes per round
          come from the form above.
        </span>
      </div>
    </section>
  );
}

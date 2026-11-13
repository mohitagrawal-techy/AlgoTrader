import type { ExitUnit, LabSpec, LabSummary, TechCatalog, TechRule, TuneNode } from "../api";
import { LineChart, timeTicks } from "../charts";
import { EXIT_LABELS, fraction, inr, inrCompact, num, shortDate, signedInr } from "../format";
import { Pnl, Status } from "../ui";
import { ruleText } from "../builder/logic/ruleText";

/*
 * What one try in the tuner changed, and what that did. The change is spelled out in words,
 * then its results are set against the version it came from: the figures, the unseen-period
 * P&L curves on one chart, and how trades ended.
 */

const SETTINGS: Record<string, string> = {
  "exits.target_frac": "Take profit",
  "exits.stop_frac": "Stop loss",
  "exits.target_unit": "Take profit unit",
  "exits.stop_unit": "Stop loss unit",
  "exits.trail": "Trailing stop",
  "exits.trail_unit": "Trailing stop unit",
  "exits.breakeven_at": "Move stop to cost after",
  "exits.exit_dte": "Time exit, days before expiry",
  "exits.square_off": "Square-off time",
  "exits.daily_stop": "Daily stop loss",
  "exits.daily_target": "Daily take profit",
  "entry.dte_min": "Earliest entry, days to expiry",
  "entry.dte_max": "Latest entry, days to expiry",
  "entry.trend": "Trend filter",
  "entry.iv_rank_min": "Minimum IV rank",
  "entry.iv_rank_max": "Maximum IV rank",
  "entry.time_from": "Enter from",
  "entry.time_to": "Enter until",
  "entry.max_trades_per_day": "Trades per day",
  "entry.reenter_after_stop": "Re-enter after a stop",
  decide_every: "Checks",
};
const UNIT_OF: Record<string, string> = {
  "exits.target_frac": "target_unit",
  "exits.stop_frac": "stop_unit",
  "exits.trail": "trail_unit",
  "exits.breakeven_at": "breakeven_unit",
};
const CHECKS: Record<string, string> = { day: "once a day", "5m": "every 5 minutes", "15m": "every 15 minutes", "1h": "every hour" };

function valueAt(spec: unknown, parts: string[]): unknown {
  let current: unknown = spec;
  for (const part of parts) {
    if (current === null || current === undefined) return undefined;
    current = (current as Record<string, unknown>)[part];
  }
  return current;
}

function amount(value: unknown, unit: ExitUnit | undefined): string {
  if (value === null || value === undefined) return "off";
  if (typeof value !== "number") return String(value);
  if (unit === "rupees") return `₹${value.toLocaleString("en-IN")} per lot`;
  if (unit === "points") return `${value} points`;
  return `${Math.round(value * 100)}% of premium`;
}

function plain(value: unknown): string {
  if (value === null || value === undefined) return "off";
  if (Array.isArray(value)) return value.length ? value.join(" or ") : "any";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "string" && /^\d\d:\d\d/.test(value)) return value.slice(0, 5);
  return String(value);
}

function legName(spec: LabSpec, index: number): string {
  const leg = spec.legs[index];
  if (!leg) return `Leg ${index + 1}`;
  return `Leg ${index + 1} (${leg.action} ${leg.option_type === "CE" ? "call" : "put"})`;
}

export interface Change {
  setting: string;
  before: string;
  after: string;
}

/** The change between two versions, in words, from the path the tuner recorded. */
export function describeChange(
  parent: LabSpec,
  child: LabSpec,
  path: string | null,
  catalog: TechCatalog | null,
): Change | null {
  if (!path) return null;
  const parts = path.split(".");
  const rule = (value: unknown) =>
    value && catalog ? ruleText(catalog, value as TechRule) : value ? "a rule" : "none";

  // A list grew by one item.
  if (parts[parts.length - 1] === "+") {
    const list = parts.slice(0, -1).join(".");
    const added = (valueAt(child, parts.slice(0, -1)) as unknown[]) ?? [];
    const item = added[added.length - 1];
    if (list === "legs") {
      const leg = item as LabSpec["legs"][number];
      return { setting: "New leg", before: "none", after: `${leg.action} ${leg.ratio} ${leg.option_type} ${leg.select} ${leg.value}` };
    }
    const where = list === "exits.rules" ? "exit rule" : list === "entry.any_rules" ? "any-of rule" : list === "entry.conditions" ? "market condition" : "entry rule";
    return { setting: `New ${where}`, before: "none", after: list.endsWith("conditions") ? JSON.stringify(item) : rule(item) };
  }

  // Inside a rule: show the whole rule before and after.
  const ruleList = ["entry.rules", "entry.any_rules", "exits.rules"].find((list) => path.startsWith(`${list}.`));
  if (ruleList) {
    const index = Number(parts[ruleList.split(".").length]);
    const holder = ruleList.split(".");
    const before = (valueAt(parent, holder) as unknown[] | undefined)?.[index];
    const after = (valueAt(child, holder) as unknown[] | undefined)?.[index];
    const kind = ruleList === "exits.rules" ? "Exit rule" : ruleList === "entry.any_rules" ? "Any-of rule" : "Entry rule";
    return { setting: `${kind} ${index + 1}`, before: rule(before), after: after === undefined ? "removed" : rule(after) };
  }

  if (parts[0] === "legs") {
    const index = Number(parts[1]);
    const field = parts[2];
    const before = valueAt(parent, parts);
    const after = valueAt(child, parts);
    const leg = parent.legs[index];
    const names: Record<string, string> = { value: leg?.select === "delta" ? "strike delta" : "strike (steps from ATM)", ratio: "quantity", expiry: "expiry", action: "buy or sell", option_type: "call or put", select: "strike chosen by" };
    if (field === undefined) return { setting: legName(parent, index), before: "present", after: "removed" };
    return { setting: `${legName(parent, index)} ${names[field] ?? field}`, before: plain(before), after: plain(after) };
  }

  if (parts[0] === "entry" && parts[1] === "conditions") {
    const index = Number(parts[2]);
    const before = parent.entry.conditions[index];
    const after = child.entry.conditions[index];
    const text = (c: typeof before | undefined) => (c ? `${c.signal} ${c.op} ${c.value}` : "removed");
    return { setting: `Market condition ${index + 1}`, before: text(before), after: text(after) };
  }

  const setting = SETTINGS[path] ?? path;
  const before = valueAt(parent, parts);
  const after = valueAt(child, parts);
  const unitKey = UNIT_OF[path];
  if (unitKey) {
    const exitsBefore = parent.exits as unknown as Record<string, ExitUnit>;
    const exitsAfter = child.exits as unknown as Record<string, ExitUnit>;
    return { setting, before: amount(before, exitsBefore[unitKey]), after: amount(after, exitsAfter[unitKey]) };
  }
  if (path === "decide_every") return { setting, before: CHECKS[String(before)] ?? plain(before), after: CHECKS[String(after)] ?? plain(after) };
  if (path === "exits.daily_stop" || path === "exits.daily_target") return { setting, before: amount(before, "rupees"), after: amount(after, "rupees") };
  return { setting, before: plain(before), after: plain(after) };
}

/** One line for a card in the tree: how the unseen Sharpe moved against the parent. */
export function SharpeShift({ node, parent }: { node: TuneNode; parent: TuneNode | undefined }) {
  if (!parent) return null;
  const before = parent.validate.sharpe;
  const after = node.validate.sharpe;
  if (before === null || before === undefined || after === null || after === undefined) {
    return <span className="tune-shift muted">Unseen Sharpe {num(before, 2)} → {num(after, 2)}</span>;
  }
  const delta = after - before;
  return (
    <span className={`tune-shift ${delta > 0 ? "up" : delta < 0 ? "down" : ""}`}>
      Unseen Sharpe {num(before, 2)} → {num(after, 2)}{" "}
      <b>
        ({delta > 0 ? "+" : ""}
        {num(delta, 2)})
      </b>
    </span>
  );
}

type Metric = { label: string; read: (s: LabSummary) => number | null | undefined; show: (v: number | null | undefined) => string; money?: boolean; lowerIsBetter?: boolean };

const METRICS: Metric[] = [
  { label: "Trades", read: (s) => s.trades, show: (v) => num(v) },
  { label: "Net P&L", read: (s) => s.net_pnl, show: (v) => signedInr(v), money: true },
  { label: "Win rate", read: (s) => s.win_rate, show: (v) => fraction(v, 0) },
  { label: "Expectancy", read: (s) => s.expectancy, show: (v) => signedInr(v), money: true },
  { label: "Sharpe", read: (s) => s.sharpe, show: (v) => num(v, 2) },
  // Shown as the size of the fall, so a smaller number is better.
  {
    label: "Max drawdown",
    read: (s) => (s.max_drawdown === null || s.max_drawdown === undefined ? null : Math.abs(s.max_drawdown)),
    show: (v) => (v === null || v === undefined ? "–" : v === 0 ? "₹0" : `−${inr(v)}`),
    money: true,
    lowerIsBetter: true,
  },
];

function Delta({ before, after, metric }: { before: number | null | undefined; after: number | null | undefined; metric: Metric }) {
  if (before === null || before === undefined || after === null || after === undefined) return <span className="muted">–</span>;
  const delta = after - before;
  if (Math.abs(delta) < 1e-9) return <span className="muted">same</span>;
  const better = metric.lowerIsBetter ? delta < 0 : delta > 0;
  const text = metric.lowerIsBetter
    ? `${delta > 0 ? "deeper by " : "shallower by "}${inr(Math.abs(delta))}`
    : metric.money
    ? signedInr(delta)
    : metric.label === "Win rate"
      ? `${delta > 0 ? "+" : ""}${Math.round(delta * 100)} pts`
      : `${delta > 0 ? "+" : ""}${num(delta, metric.label === "Trades" ? 0 : 2)}`;
  return <span className={better ? "pnl-up" : "pnl-down"}>{text}</span>;
}

function MetricsTable({ parent, node }: { parent: TuneNode; node: TuneNode }) {
  return (
    <div className="table-wrap">
      <table className="table compact-table tune-compare-table">
        <thead>
          <tr>
            <th />
            <th className="right">Before</th>
            <th className="right">This try</th>
            <th className="right">Change</th>
          </tr>
        </thead>
        {(["validate", "design"] as const).map((period) => (
          <tbody key={period}>
            <tr className="tune-period">
              <td colSpan={4}>{period === "validate" ? "Unseen sessions (decides keep or drop)" : "Design sessions (what the AI saw)"}</td>
            </tr>
            {METRICS.map((metric) => {
              const before = metric.read(parent[period]);
              const after = metric.read(node[period]);
              return (
                <tr key={metric.label}>
                  <td>{metric.label}</td>
                  <td className="right">{metric.show(before)}</td>
                  <td className="right">
                    {metric.money && !metric.lowerIsBetter ? (
                      <Pnl value={after ?? 0}>{metric.show(after)}</Pnl>
                    ) : (
                      metric.show(after)
                    )}
                  </td>
                  <td className="right">
                    <Delta before={before} after={after} metric={metric} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        ))}
      </table>
    </div>
  );
}

function Curves({ parent, node }: { parent?: TuneNode; node: TuneNode }) {
  const toPoints = (curve: TuneNode["validate_curve"]) =>
    (curve ?? []).map((point) => ({ x: new Date(`${point.day}T00:00:00+05:30`).getTime(), y: point.pnl }));
  const series = [
    ...(parent ? [{ name: "Before", color: "var(--tick)", points: toPoints(parent.validate_curve) }] : []),
    { name: parent ? "This try" : "Starting version", color: "var(--accent)", points: toPoints(node.validate_curve) },
  ];
  if (series.every((s) => s.points.length < 2)) return <p className="secondary">No unseen-period P&amp;L to chart.</p>;
  return (
    <LineChart
      series={series}
      height={190}
      includeZero
      tickX={timeTicks}
      label="Unseen-period cumulative P&L, before and after the change"
      formatX={(x) => shortDate(new Date(x + 19800000).toISOString().slice(0, 10))}
      formatY={inrCompact}
      formatTipY={(y) => signedInr(y)}
    />
  );
}

function ExitMix({ parent, node }: { parent: TuneNode; node: TuneNode }) {
  const reasons = Array.from(new Set([...Object.keys(parent.design_exits ?? {}), ...Object.keys(node.design_exits ?? {})]));
  if (!reasons.length) return null;
  const max = Math.max(1, ...reasons.flatMap((r) => [parent.design_exits?.[r] ?? 0, node.design_exits?.[r] ?? 0]));
  return (
    <div className="tune-exits">
      {reasons.map((reason) => {
        const before = parent.design_exits?.[reason] ?? 0;
        const after = node.design_exits?.[reason] ?? 0;
        return (
          <div className="tune-exit-row" key={reason}>
            <span className="tune-exit-name">{EXIT_LABELS[reason] ?? reason.replace(/_/g, " ")}</span>
            <span className="tune-exit-bars" aria-label={`${before} before, ${after} after`}>
              <span className="before" style={{ width: `${(before / max) * 100}%` }} />
              <span className="after" style={{ width: `${(after / max) * 100}%` }} />
            </span>
            <span className="tune-exit-count">
              {before} → {after}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function TuneCompare({
  node,
  parent,
  catalog,
  number,
  onClose,
}: {
  node: TuneNode;
  parent: TuneNode | undefined;
  catalog: TechCatalog | null;
  /** The try's place in the run order, as numbered in the all-tests table. */
  number?: number;
  onClose: () => void;
}) {
  const change = parent ? describeChange(parent.spec, node.spec, node.path, catalog) : null;
  const state = node.depth === 0 ? null : node.accepted ? { tone: "good" as const, label: "Kept" } : { tone: "critical" as const, label: "Not kept" };
  return (
    <div className="tune-compare">
      <header className="tune-compare-head">
        <div>
          <h3>{node.depth === 0 ? "Starting version" : `Try ${number ?? node.depth}: what changed`}</h3>
          {state && <Status tone={state.tone}>{state.label}</Status>}
        </div>
        <button type="button" className="icon-button" aria-label="Close comparison" onClick={onClose}>
          ✕
        </button>
      </header>

      {change ? (
        <div className="tune-change">
          <span className="tune-change-setting">{change.setting}</span>
          <div className="tune-change-values">
            <span className="tune-before">{change.before}</span>
            <span className="tune-arrow" aria-hidden>
              →
            </span>
            <span className="tune-after">{change.after}</span>
          </div>
          {node.reason && <p className="secondary">Why try it: {node.reason}</p>}
          {node.verdict && <p className="secondary">Verdict: {node.verdict}</p>}
        </div>
      ) : (
        <p className="secondary">The strategy as you built it. Every try below changes one setting of a version like this.</p>
      )}

      {parent ? (
        <>
          <MetricsTable parent={parent} node={node} />
          <h4>Unseen-period P&amp;L</h4>
          <div className="tune-legend">
            <span className="tune-key before">Before</span>
            <span className="tune-key after">This try</span>
          </div>
          <Curves parent={parent} node={node} />
          <h4>How design-period trades ended</h4>
          <ExitMix parent={parent} node={node} />
        </>
      ) : (
        <>
          <h4>Unseen-period P&amp;L</h4>
          <Curves node={node} />
        </>
      )}
    </div>
  );
}

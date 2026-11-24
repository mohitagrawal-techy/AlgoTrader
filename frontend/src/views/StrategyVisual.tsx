import type { ReactNode } from "react";
import type { ExitUnit, LabLeg, LabSpec, TechCatalog } from "../api";
import { joinWord, ruleText } from "../builder/logic/ruleText";

/*
 * A picture of one strategy: when it checks, what must be true to enter, what it trades, and
 * what closes the trade. Below it, an exit map plays one made-up trade through the exits so
 * the stop, target and trailing stop can be seen acting together.
 */

const CHECKS: Record<string, string> = {
  day: "Once a day, at the close",
  "5m": "Every 5 minutes",
  "15m": "Every 15 minutes",
  "1h": "Every hour",
};

function hhmm(value: string | null | undefined): string {
  return value ? value.slice(0, 5) : "";
}

function amount(value: number | null | undefined, unit: ExitUnit | undefined): string {
  if (value === null || value === undefined) return "";
  if (unit === "rupees") return `₹${value.toLocaleString("en-IN")} per lot`;
  if (unit === "points") return `${value} points`;
  return `${Math.round(value * 100)}% of premium`;
}

function strikeText(leg: LabLeg): string {
  if (leg.select === "delta") return `delta ${leg.value}`;
  if (leg.select === "premium") return `near ₹${leg.value}`;
  if (!leg.value) return "ATM";
  return `ATM${leg.value > 0 ? "+" : ""}${leg.value}`;
}

function LegToken({ leg }: { leg: LabLeg }) {
  const kind = leg.option_type === "CE" ? "call" : "put";
  return (
    <span className={`sv-leg sv-${kind} ${leg.action === "sell" ? "sv-sell" : ""}`}>
      <b>{leg.action === "buy" ? "Buy" : "Sell"}</b>
      {leg.ratio > 1 ? ` ${leg.ratio}×` : ""} {kind === "call" ? "Call" : "Put"} {strikeText(leg)}
      {leg.expiry ? <small> expiry +{leg.expiry}</small> : null}
    </span>
  );
}

function Step({ title, children }: { title: string; children: ReactNode }) {
  return (
    <li className="sv-step">
      <span className="sv-dot" aria-hidden />
      <div className="sv-body">
        <h4>{title}</h4>
        {children}
      </div>
    </li>
  );
}

// ---- The exit map ----------------------------------------------------------------------

const W = 320;
const H = 176;
const PAD = { left: 44, right: 10, top: 14, bottom: 22 };
const STEPS = 60;
// Made-up premium path, as a fraction of the entry premium: a rise, a pullback, a higher high,
// then a fall. Shaped so each exit has a chance to act.
const ANCHORS: [number, number][] = [
  [0, 0],
  [0.12, -0.04],
  [0.3, 0.22],
  [0.4, 0.14],
  [0.58, 0.5],
  [0.72, 0.3],
  [0.86, 0.12],
  [1, -0.02],
];

function pathAt(t: number, scale: number): number {
  for (let i = 1; i < ANCHORS.length; i++) {
    const [t1, v1] = ANCHORS[i];
    const [t0, v0] = ANCHORS[i - 1];
    if (t <= t1) return (v0 + ((t - t0) / (t1 - t0)) * (v1 - v0)) * scale;
  }
  return ANCHORS[ANCHORS.length - 1][1] * scale;
}

/** Converts an exit amount to a fraction of premium. Other units are drawn at a nominal size. */
function asFraction(value: number | null | undefined, unit: ExitUnit | undefined, nominal: number) {
  if (value === null || value === undefined) return null;
  return unit === "premium" || unit === undefined ? value : nominal;
}

function ExitMap({ spec }: { spec: LabSpec }) {
  const exits = spec.exits;
  const stop = asFraction(exits.stop_frac, exits.stop_unit, 0.2) ?? 0.5;
  const target = asFraction(exits.target_frac, exits.target_unit, 0.8) ?? 1;
  const trail = asFraction(exits.trail, exits.trail_unit, 0.15);
  const breakeven = asFraction(exits.breakeven_at, exits.breakeven_unit, 0.2);
  // Scale the made-up path so it reaches beyond the trailing distance but stays readable.
  const scale = Math.max(0.3, Math.min(1.2, (trail ?? stop) * 2.6, target * 0.9));

  const points: { t: number; v: number; s: number }[] = [];
  let peak = 0;
  let exitAt: { t: number; v: number; why: string } | null = null;
  for (let i = 0; i <= STEPS; i++) {
    const t = i / STEPS;
    const v = pathAt(t, scale);
    peak = Math.max(peak, v);
    let s = -stop;
    if (breakeven !== null && peak >= breakeven && s < 0) s = 0;
    if (trail !== null && peak > 0 && peak - trail > s) s = peak - trail;
    points.push({ t, v, s });
    if (!exitAt && i > 0) {
      if (v >= target) exitAt = { t, v: target, why: "Target" };
      else if (v <= s)
        exitAt = {
          t,
          v: s,
          why: s > -stop + 1e-9 ? (trail !== null && s > 0 ? "Trailing stop" : "Stop at cost") : "Stop loss",
        };
    }
  }
  if (!exitAt) {
    const last = points[points.length - 1];
    exitAt = { t: 1, v: last.v, why: exits.square_off ? `Square-off ${hhmm(exits.square_off)}` : "Time exit" };
  }
  const shown = points.filter((p) => p.t <= exitAt!.t);
  const top = Math.max(peak + 0.1, Math.min(target, peak + 0.35));
  const bottom = Math.min(-stop - 0.08, -0.1);
  const x = (t: number) => PAD.left + t * (W - PAD.left - PAD.right);
  const y = (v: number) => PAD.top + ((top - v) / (top - bottom)) * (H - PAD.top - PAD.bottom);
  const line = (key: "v" | "s") => shown.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p[key]).toFixed(1)}`).join("");
  const targetVisible = target <= top;
  const pct = (v: number) => `${v > 0 ? "+" : ""}${Math.round(v * 100)}%`;

  return (
    <figure className="sv-map">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Illustration of how the exits act on one trade">
        <line className="sv-axis" x1={PAD.left} x2={W - PAD.right} y1={y(0)} y2={y(0)} />
        <text className="sv-tick" x={PAD.left - 4} y={y(0) + 3} textAnchor="end">
          Entry
        </text>
        <line className="sv-stop-line" x1={PAD.left} x2={W - PAD.right} y1={y(-stop)} y2={y(-stop)} />
        <text className="sv-tick" x={PAD.left - 4} y={y(-stop) + 3} textAnchor="end">
          {exits.stop_unit === "premium" || !exits.stop_unit ? pct(-stop) : "Stop"}
        </text>
        {targetVisible ? (
          <>
            <line className="sv-target-line" x1={PAD.left} x2={W - PAD.right} y1={y(target)} y2={y(target)} />
            <text className="sv-tick" x={PAD.left - 4} y={y(target) + 3} textAnchor="end">
              {exits.target_unit === "premium" || !exits.target_unit ? pct(target) : "Target"}
            </text>
          </>
        ) : (
          <text className="sv-note" x={W - PAD.right} y={PAD.top + 2} textAnchor="end">
            Target {amount(exits.target_frac, exits.target_unit)} is above this view
          </text>
        )}
        {(trail !== null || breakeven !== null) && <path className="sv-trail" d={line("s")} />}
        <path className="sv-price" d={line("v")} />
        <circle className="sv-entry" cx={x(0)} cy={y(0)} r={3.5} />
        <circle className="sv-exit" cx={x(exitAt.t)} cy={y(exitAt.v)} r={4} />
        <text
          className="sv-exit-label"
          x={Math.min(x(exitAt.t) + 6, W - PAD.right)}
          y={Math.max(y(exitAt.v) - 7, PAD.top + 8)}
          textAnchor={x(exitAt.t) > W * 0.7 ? "end" : "start"}
        >
          {exitAt.why}
        </text>
        <text className="sv-tick" x={PAD.left} y={H - 6}>
          Entry
        </text>
        <text className="sv-tick" x={W - PAD.right} y={H - 6} textAnchor="end">
          {exits.square_off ? `Square-off ${hhmm(exits.square_off)}` : `${exits.exit_dte}d before expiry`}
        </text>
      </svg>
      <figcaption>
        <span className="sv-key sv-key-price">Option premium</span>
        {(trail !== null || breakeven !== null) && <span className="sv-key sv-key-trail">Stop as it moves</span>}
        <span className="sv-key sv-key-stop">Starting stop</span>
        <span className="muted">One made-up trade, not real prices.</span>
      </figcaption>
    </figure>
  );
}

// ---- The whole picture -----------------------------------------------------------------

export function StrategyVisual({
  spec,
  catalog,
  signals,
}: {
  spec: LabSpec;
  catalog: TechCatalog | null;
  signals?: { name: string; label: string }[];
}) {
  const entry = spec.entry;
  const exits = spec.exits;
  const every = spec.decide_every ?? "day";
  const rules = entry.rules ?? [];
  const anyRules = entry.any_rules ?? [];
  const signalLabel = (name: string) => signals?.find((s) => s.name === name)?.label ?? name;
  const text = (rule: (typeof rules)[number]) => (catalog ? ruleText(catalog, rule) : rule.type);
  const market: string[] = [];
  if (entry.trend.length) market.push(`Trend is ${entry.trend.join(" or ")}`);
  if (entry.iv_rank_min !== null) market.push(`IV rank at least ${entry.iv_rank_min}`);
  if (entry.iv_rank_max !== null) market.push(`IV rank at most ${entry.iv_rank_max}`);
  for (const c of entry.conditions ?? []) market.push(`${signalLabel(c.signal)} ${c.op} ${c.value}`);
  const window =
    entry.time_from || entry.time_to ? `${hhmm(entry.time_from) || "09:15"} to ${hhmm(entry.time_to) || "15:25"}` : "";

  const exitItems: { label: string; tone?: string }[] = [
    { label: `Take profit at ${amount(exits.target_frac, exits.target_unit)}`, tone: "good" },
    { label: `Stop loss at ${amount(exits.stop_frac, exits.stop_unit)}`, tone: "bad" },
  ];
  if (exits.trail)
    exitItems.push({
      label: exits.trail_step
        ? `Stop rises ${amount(exits.trail, exits.trail_unit)} for every ${amount(exits.trail_step, exits.trail_unit)} gained`
        : `Trailing stop ${amount(exits.trail, exits.trail_unit)} behind the best price`,
      tone: "bad",
    });
  if (exits.breakeven_at)
    exitItems.push({ label: `Stop moves to cost after ${amount(exits.breakeven_at, exits.breakeven_unit)}` });
  for (const rule of exits.rules ?? []) exitItems.push({ label: `Signal: ${text(rule)}` });
  if (exits.square_off) exitItems.push({ label: `Square off by ${hhmm(exits.square_off)}` });
  else exitItems.push({ label: `Time exit ${exits.exit_dte} day${exits.exit_dte === 1 ? "" : "s"} before expiry` });
  if (exits.daily_stop) exitItems.push({ label: `Day stops at a ₹${exits.daily_stop.toLocaleString("en-IN")} loss` });
  if (exits.daily_target) exitItems.push({ label: `Day stops at a ₹${exits.daily_target.toLocaleString("en-IN")} gain` });

  return (
    <div className="sv">
      <ol className="sv-flow">
        <Step title="Checks">
          <p>
            {CHECKS[every]}
            {window && <>, {window}</>}
          </p>
          <p className="muted">{spec.symbols.length ? spec.symbols.join(", ") : "Every index"}</p>
        </Step>
        <Step title="Enters when">
          {rules.length > 0 && (
            <ul className="sv-rules">
              {rules.map((rule, i) => (
                <li key={i}>
                  {i > 0 && <b>{joinWord(rules, i)} </b>}
                  {text(rule)}
                </li>
              ))}
            </ul>
          )}
          {anyRules.length > 0 && (
            <div className="sv-any">
              <span className="muted">and any one of</span>
              <ul className="sv-rules">
                {anyRules.map((rule, i) => (
                  <li key={i}>{text(rule)}</li>
                ))}
              </ul>
            </div>
          )}
          {market.length > 0 && (
            <ul className="sv-rules sv-market">
              {market.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}
          {!rules.length && !anyRules.length && !market.length && (
            <p className="muted">No signal rules: it enters whenever the risk limits allow.</p>
          )}
          <p className="muted">
            {entry.dte_min}–{entry.dte_max} days to expiry
            {entry.max_trades_per_day ? `, at most ${entry.max_trades_per_day} a day` : ""}
            {entry.reenter_after_stop === false ? ", no re-entry after a stop" : ""}
          </p>
        </Step>
        <Step title="Trades">
          {(spec.cases ?? []).length > 0 && <p className="muted">When the rules above hold</p>}
          <div className="sv-legs">
            {spec.legs.map((leg, i) => (
              <LegToken leg={leg} key={i} />
            ))}
          </div>
          {(spec.cases ?? []).map((item, index) => (
            <div className="sv-case" key={index}>
              <p className="muted">
                Or, {item.name.trim() || `case ${index + 2}`}, when
              </p>
              {(item.rules ?? []).length > 0 ? (
                <ul className="sv-rules">
                  {(item.rules ?? []).map((rule, i, list) => (
                    <li key={i}>
                      {i > 0 && <b>{joinWord(list, i)} </b>}
                      {text(rule)}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">the shared rules hold</p>
              )}
              <div className="sv-legs">
                {item.legs.map((leg, i) => (
                  <LegToken leg={leg} key={i} />
                ))}
              </div>
            </div>
          ))}
        </Step>
        <Step title="Exits on the first of">
          <ul className="sv-exits">
            {exitItems.map((item) => (
              <li key={item.label} className={item.tone ? `sv-${item.tone}` : undefined}>
                {item.label}
              </li>
            ))}
          </ul>
        </Step>
      </ol>
      <ExitMap spec={spec} />
    </div>
  );
}

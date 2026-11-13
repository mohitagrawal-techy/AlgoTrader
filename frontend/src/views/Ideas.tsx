import { useMemo, useState } from "react";
import type { AiReview, Idea, IdeaLeg, ScorePart } from "../api";
import { LineChart } from "../charts";
import {
  GROUP_LABELS,
  PARAM_LABELS,
  PROFILE_LABELS,
  STRATEGY_LABELS,
  fraction,
  inr,
  inrCompact,
  num,
  shortDate,
  signedInr,
} from "../format";
import {
  Empty,
  Meter,
  Panel,
  Pnl,
  LabeledSelect,
  Status,
  Tile,
  Table,
  Tiles,
} from "../components/ui";

function legText(leg: IdeaLeg, withExpiry = false): string {
  const side = leg.side > 0 ? "+" : "−";
  const ratio = leg.ratio > 1 ? `${leg.ratio}×` : "";
  const expiry = withExpiry ? ` ${shortDate(leg.expiry)}` : "";
  return `${side}${ratio}${num(leg.strike)} ${leg.option_type}${expiry}`;
}

function premiumText(debit: number): string {
  return debit >= 0 ? `Pay ${inr(debit)}` : `Get ${inr(-debit)}`;
}

function Payoff({ idea }: { idea: Idea }) {
  const markers = [
    { x: idea.spot, label: "Spot" },
    ...idea.structure.breakevens.map((x) => ({ x, label: "Breakeven" })),
  ];
  const series = useMemo(
    () => [
      {
        name: "P&L at front expiry",
        color: "var(--series-1)",
        points: idea.payoff.map(([x, y]) => ({ x, y })),
      },
    ],
    [idea],
  );
  return (
    <LineChart
      series={series}
      height={230}
      polarity
      includeZero
      markers={markers}
      label={`Payoff at expiry for one lot of ${STRATEGY_LABELS[idea.strategy]}`}
      formatX={(x) => num(x)}
      formatY={inrCompact}
      formatTipX={(x) => `Index at ${num(x)}`}
      formatTipY={(y) => signedInr(y)}
    />
  );
}

function ParamRow({ part }: { part: ScorePart }) {
  const missing = part.adjusted === null;
  const preference =
    part.prefer === "low"
      ? "lower is better"
      : part.prefer === "mid"
        ? "middle is best"
        : "";
  return (
    <div
      className={`param ${missing ? "param-missing" : ""}`}
      title={part.note}
    >
      <span className="param-name">
        {PARAM_LABELS[part.name] ?? part.name}
        {preference && <small>{preference}</small>}
      </span>
      <Meter value={part.adjusted ?? 0} />
      <span className="param-score">
        {missing ? "No data" : num(part.adjusted, 0)}
      </span>
    </div>
  );
}

function Breakdown({ parts }: { parts: ScorePart[] }) {
  const groups = useMemo(() => {
    const grouped = new Map<string, ScorePart[]>();
    for (const part of parts) {
      grouped.set(part.group, [...(grouped.get(part.group) ?? []), part]);
    }
    return [...grouped.entries()];
  }, [parts]);
  return (
    <div className="breakdown">
      {groups.map(([group, members]) => (
        <div key={group}>
          <h4>{GROUP_LABELS[group] ?? group}</h4>
          {members.map((part) => (
            <ParamRow key={part.name} part={part} />
          ))}
        </div>
      ))}
    </div>
  );
}

function AiBox({ ai }: { ai: AiReview }) {
  if (ai.status !== "ok") {
    return (
      <div className="ai-box">
        <Status tone="neutral">
          {ai.status === "over_limit"
            ? "AI limit reached"
            : "AI review unavailable"}
        </Status>
        <p className="secondary">This idea uses its numeric score only.</p>
      </div>
    );
  }
  return (
    <div className="ai-box">
      <header>
        <h3>AI review</h3>
        <span className="muted">
          {ai.model}
          {ai.cached ? ", cached" : ""}
        </span>
      </header>
      <p>
        <b>Thesis.</b> {ai.thesis}
      </p>
      <p>
        <b>Bear case.</b> {ai.bear_case}
      </p>
      <p className="secondary">
        {ai.veto
          ? "The AI vetoed this idea."
          : ai.adjustment < 0
            ? `The AI lowered the score by ${-ai.adjustment}.`
            : "The AI left the score unchanged."}
      </p>
    </div>
  );
}

function Detail({ idea }: { idea: Idea }) {
  const { structure, plan, stress } = idea;
  return (
    <div className="detail">
      <header className="detail-header">
        <div>
          <div className="eyebrow">
            {idea.symbol} · {shortDate(idea.front_expiry)} · {structure.dte}{" "}
            days
          </div>
          <h2>{STRATEGY_LABELS[idea.strategy] ?? idea.strategy}</h2>
          <div className="legs">
            {idea.legs.map((leg) => (
              <code key={`${leg.expiry}-${leg.strike}-${leg.option_type}`}>
                {legText(leg, new Set(idea.legs.map((l) => l.expiry)).size > 1)}
              </code>
            ))}
          </div>
        </div>
        <div className="detail-score">
          <span>{num(idea.score, 0)}</span>
          <small>
            {idea.base_score !== null &&
            idea.score !== null &&
            idea.score < idea.base_score
              ? `numeric ${num(idea.base_score, 0)}, AI ${num(idea.score - idea.base_score, 0)}`
              : "score of 100"}
          </small>
        </div>
      </header>

      {idea.tradable ? (
        <Status tone="good">
          Passes all checks at {idea.lots} {idea.lots === 1 ? "lot" : "lots"}
        </Status>
      ) : (
        <div className="reject-box">
          <Status tone="critical">Not tradable</Status>
          <ul>
            {idea.rejections.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}

      {idea.ai && <AiBox ai={idea.ai} />}

      <h3>Payoff, one lot</h3>
      <Payoff idea={idea} />

      <Tiles columns={3}>
        <Tile label="Premium" value={premiumText(structure.entry_debit)} />
        <Tile
          label="Maximum loss"
          value={structure.bounded ? inr(structure.max_loss) : "Undefined"}
          hint={
            structure.bounded
              ? undefined
              : `Plan: ${inr(plan.planned_loss)} at stop`
          }
        />
        <Tile label="Reward within 2σ" value={inr(structure.reward)} />
        <Tile
          label="Probability of profit"
          value={fraction(structure.pop, 0)}
          hint="Model estimate"
        />
        <Tile
          label="Expected value"
          value={
            <Pnl value={structure.net_ev}>{signedInr(structure.net_ev)}</Pnl>
          }
          hint="After costs, model estimate"
        />
        <Tile
          label="Stress, worst case"
          value={
            <Pnl value={stress.worst_pnl}>{signedInr(stress.worst_pnl)}</Pnl>
          }
          hint={`Spot ${stress.spot_move_pct > 0 ? "+" : ""}${stress.spot_move_pct}%, IV ${
            stress.iv_shift_pct > 0 ? "+" : ""
          }${stress.iv_shift_pct}%`}
        />
      </Tiles>

      <h3>Exit plan, one lot</h3>
      <dl className="facts">
        <div>
          <dt>Breakevens</dt>
          <dd>
            {structure.breakevens.length
              ? structure.breakevens.map((b) => num(b)).join(", ")
              : "None"}
          </dd>
        </div>
        <div>
          <dt>Spot</dt>
          <dd>{num(idea.spot, 2)}</dd>
        </div>
        <div>
          <dt>Target</dt>
          <dd>{signedInr(plan.target_pnl)}</dd>
        </div>
        <div>
          <dt>Stop</dt>
          <dd>{signedInr(plan.stop_pnl)}</dd>
        </div>
        <div>
          <dt>Time exit</dt>
          <dd>
            {plan.exit_dte === 0
              ? "15:00 on expiry day"
              : `15:00, ${plan.exit_dte} day${plan.exit_dte > 1 ? "s" : ""} before expiry`}
          </dd>
        </div>
        <div>
          <dt>Costs and exit spread</dt>
          <dd>{inr(structure.round_trip_cost + structure.exit_spread)}</dd>
        </div>
        <div>
          <dt>Estimated margin</dt>
          <dd>{inr(structure.margin)}</dd>
        </div>
        <div>
          <dt>Theta per day</dt>
          <dd>{signedInr(structure.net_theta)}</dd>
        </div>
      </dl>

      <h3>
        Why it scored {num(idea.score, 0)}
        <small> {fraction(idea.coverage, 0)} of scoring weight has data</small>
      </h3>
      <Breakdown parts={idea.parts} />
    </div>
  );
}

type Show = "tradable" | "all";

export function IdeasView({ ideas }: { ideas: Idea[] }) {
  const [symbol, setSymbol] = useState("all");
  const [profile, setProfile] = useState("all");
  const [show, setShow] = useState<Show>("tradable");
  const [selected, setSelected] = useState<string | null>(null);

  const symbols = useMemo(
    () => [...new Set(ideas.map((i) => i.symbol))],
    [ideas],
  );
  const profiles = useMemo(
    () => [...new Set(ideas.map((i) => i.profile))],
    [ideas],
  );
  const visible = ideas.filter(
    (idea) =>
      (symbol === "all" || idea.symbol === symbol) &&
      (profile === "all" || idea.profile === profile) &&
      (show === "all" || idea.tradable),
  );
  const tradableCount = ideas.filter((idea) => idea.tradable).length;
  const current =
    visible.find((idea) => idea.id === selected) ?? visible[0] ?? null;

  if (!ideas.length) {
    return (
      <Empty title="No ideas yet">
        Log in to Upstox and press Refresh, or leave{" "}
        <code>algoverse collect</code> running during market hours.
      </Empty>
    );
  }

  return (
    <>
      <div className="filters">
        <LabeledSelect
          label="Underlying"
          value={symbol}
          onChange={setSymbol}
          options={[
            { value: "all", label: "All" },
            ...symbols.map((s) => ({ value: s, label: s })),
          ]}
        />
        <LabeledSelect
          label="Style"
          value={profile}
          onChange={setProfile}
          options={[
            { value: "all", label: "All" },
            ...profiles.map((p) => ({
              value: p,
              label: PROFILE_LABELS[p] ?? p,
            })),
          ]}
        />
        <LabeledSelect
          label="Show"
          value={show}
          onChange={setShow}
          options={[
            { value: "tradable", label: "Tradable only" },
            { value: "all", label: "Include rejected" },
          ]}
        />
        <span className="filter-count">
          {visible.length} shown · {tradableCount} tradable of {ideas.length}
        </span>
      </div>
      <div className={visible.length === 0 ? "" : "split"}>
        <Panel title="Ranked ideas">
          {visible.length === 0 ? (
            <Empty
              title={
                ideas.length ? "No tradable ideas right now" : "No ideas yet"
              }
            >
              {ideas.length
                ? `All ${ideas.length} ideas from the latest chains were rejected. Show “Include rejected” to see the reason for each.`
                : "Ideas appear after the collector ranks the first option chains of the session."}
            </Empty>
          ) : (
            <Table className="table-ideas">
              <thead>
                <tr>
                  <th>Score</th>
                  <th>Strategy</th>
                  <th>Expiry</th>
                  <th>Legs</th>
                  <th className="right">Premium</th>
                  <th className="right">Max loss</th>
                  <th className="right">POP</th>
                  <th className="right">Net EV</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((idea) => (
                  <tr
                    key={idea.id}
                    className={idea.id === current?.id ? "selected" : ""}
                    onClick={() => setSelected(idea.id)}
                    tabIndex={0}
                    onKeyDown={(event) =>
                      event.key === "Enter" && setSelected(idea.id)
                    }
                  >
                    <td className="score-cell">
                      <b>{num(idea.score, 0)}</b>
                      <Meter value={idea.score ?? 0} />
                    </td>
                    <td>
                      <div className="primary">
                        {STRATEGY_LABELS[idea.strategy]}
                      </div>
                      <div className="secondary">{idea.symbol}</div>
                    </td>
                    <td>
                      <div className="primary">
                        {shortDate(idea.front_expiry)}
                      </div>
                      <div className="secondary">{idea.structure.dte}d</div>
                    </td>
                    <td className="legs-cell">
                      {idea.legs.map((l) => legText(l)).join("  ")}
                    </td>
                    <td className="right">
                      {premiumText(idea.structure.entry_debit)}
                    </td>
                    <td className="right">
                      {idea.structure.bounded
                        ? inr(idea.structure.max_loss)
                        : "Undefined"}
                    </td>
                    <td className="right">{fraction(idea.structure.pop, 0)}</td>
                    <td className="right">
                      <Pnl value={idea.structure.net_ev}>
                        {signedInr(idea.structure.net_ev)}
                      </Pnl>
                    </td>
                    <td title={idea.rejections.join("\n")}>
                      {idea.tradable ? (
                        <Status tone="good">
                          {idea.lots} {idea.lots === 1 ? "lot" : "lots"}
                        </Status>
                      ) : (
                        <Status tone="critical">Rejected</Status>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Panel>
        {visible.length > 0 && (
          <aside className="panel detail-panel">
            {current && <Detail idea={current} />}
          </aside>
        )}
      </div>
    </>
  );
}

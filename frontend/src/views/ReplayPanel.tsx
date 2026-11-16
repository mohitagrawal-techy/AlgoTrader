import { useState } from "react";
import { ChartChip, cx } from "../components/ui";
import type { EntryReading, ReplayData, ReplayTrade } from "../api";
import { signedInr } from "../format";
import {
  indexLevel,
  legText,
  positionDelta,
  stateAt,
  stopAt,
  wallText,
} from "./replay";
import { legPrice, TradeChart } from "./TradeChart";

export const EXIT_REASONS: Record<string, string> = {
  target: "profit target",
  stop: "stop loss",
  time: "time exit",
  expiry_settlement: "held to expiry",
  daily_loss_halt: "daily loss limit",
  end_of_test: "end of the replay",
  end_of_test_at_last_mark: "end of the replay",
  square_off: "square-off time",
  exit_rule: "exit rule",
  trailing_stop: "trailing stop",
  stop_at_cost: "stop at cost",
  daily_stop: "daily stop loss",
  daily_target: "daily take profit",
};

export const SPEEDS = [1, 2, 4, 10, 25];

function Pnl({ value }: { value: number }) {
  return (
    <b className={value >= 0 ? "pnl-up" : "pnl-down"}>{signedInr(value)}</b>
  );
}

const price = (value: number) =>
  value.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

/** One rule's reading at the decision, such as "15m close 25,229.45 above ORB high 25,209.85". */
function readingText(item: EntryReading, intradayTimes: boolean): string {
  if (item.value_text) return `at ${item.value_text}`;
  if (item.left === undefined) {
    return item.candle
      ? `on the ${wallText(item.candle, intradayTimes)} candle`
      : "";
  }
  const left = item.left === null ? "n/a" : price(item.left);
  const right =
    item.right === null || item.right === undefined ? null : price(item.right);
  let text =
    right === null ? `value ${left}` : `${left} ${item.op ?? "vs"} ${right}`;
  if (
    item.left_before !== undefined &&
    item.left_before !== null &&
    item.right_before !== undefined &&
    item.right_before !== null
  ) {
    text += `, from ${price(item.left_before)} vs ${price(item.right_before)} the candle before`;
  }
  if (item.candle)
    text += `, on the ${wallText(item.candle, intradayTimes)} candle`;
  return text;
}

const points = (value: number) =>
  `${value >= 0 ? "+" : "−"}${Math.abs(value).toLocaleString("en-IN", { maximumFractionDigits: 1 })} pts`;

/** Stop and target as index levels (by the entry delta), as option prices and in rupees. */
function LevelsTable({
  trade,
  stopPnl,
  stopPrice,
  targetPrice,
  stopLabel = "Stop loss",
}: {
  trade: ReplayTrade;
  stopPnl: number | null;
  stopPrice: number | null;
  targetPrice: number | null;
  stopLabel?: string;
}) {
  const single = trade.legs.length === 1 ? trade.legs[0] : null;
  const delta = positionDelta(trade);
  const rows: [string, number | null, number | null][] = [
    [stopLabel, stopPnl, stopPrice],
    ["Target", trade.target_pnl, targetPrice],
  ];
  return (
    <>
      <table className="replay-levels replay-levels-grid">
        <thead>
          <tr>
            <th />
            <th className="right">Index</th>
            <th className="right">Move</th>
            <th className="right">Option</th>
            <th className="right">P&amp;L</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Entry</td>
            <td className="right">
              {trade.entry_spot ? price(trade.entry_spot) : "–"}
            </td>
            <td />
            <td className="right">
              {single ? price(single.entry_price) : "–"}
            </td>
            <td />
          </tr>
          {rows.map(([label, pnl, option]) => {
            const level = indexLevel(trade, pnl);
            return (
              <tr key={label}>
                <td>{label}</td>
                <td className="right">{level ? price(level.price) : "–"}</td>
                <td className="right muted">
                  {level ? points(level.points) : ""}
                </td>
                <td className="right">
                  {option === null ? "–" : price(option)}
                </td>
                <td className="right">
                  {pnl === null ? (
                    <span className="muted">none</span>
                  ) : (
                    <Pnl value={pnl} />
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="muted replay-plan">
        {delta === null
          ? "Index levels need the delta at the fill, which this backtest predates. Test it again to see them."
          : `Index move = P&L ÷ (delta ${(delta / trade.legs.reduce((sum, leg) => sum + leg.units, 0)).toFixed(2)} × ${trade.legs.reduce((sum, leg) => sum + leg.units, 0).toLocaleString("en-IN")} units), using the delta at the fill. Gamma and time decay move the real exit point, so the option price decides the exit; the index level is a guide.`}
      </p>
    </>
  );
}

/** Why the entry rules held at the decision, rule by rule. */
function EntryReasons({
  trade,
  intradayTimes,
  decideEvery,
}: {
  trade: ReplayTrade;
  intradayTimes: boolean;
  decideEvery: string;
}) {
  return (
    <div className="replay-why">
      {trade.decide_time ? (
        <p className="muted">
          The rules held at {wallText(trade.decide_time, intradayTimes)}, on
          finished candles only. The order filled at the next check
          {intradayTimes ? ` (every ${decideEvery})` : ""},{" "}
          {wallText(trade.open_time, intradayTimes)}.
        </p>
      ) : null}
      {trade.why && trade.why.length > 0 ? (
        <ul>
          {trade.why.map((item, index) => (
            <li key={index}>
              <span className="replay-why-rule">
                {item.join && <b>{item.join === "or" ? "Or " : "And "}</b>}
                {item.any_of ? "One of: " : ""}
                {item.rule}
              </span>
              <span className="muted">
                {readingText(item, intradayTimes)}
                {!item.holds && " (did not hold; the other side of an OR did)"}
              </span>
            </li>
          ))}
        </ul>
      ) : trade.why ? (
        <p className="muted">
          No entry rules: it trades whenever the expiry window fits.
        </p>
      ) : (
        <p className="muted">
          This backtest is older than the recorded reasons. Test it again to see
          them.
        </p>
      )}
    </div>
  );
}

interface ControlsProps {
  playing: boolean;
  speed: number;
  atStart: boolean;
  atEnd: boolean;
  hasNextTrade: boolean;
  label: string;
  onPlay: () => void;
  onStep: (by: number) => void;
  onRestart: () => void;
  onNextTrade: () => void;
  onSpeed: (speed: number) => void;
  onExit: () => void;
  /** The jump button's label and hint, "Next trade" unless set. */
  nextLabel?: string;
  nextTitle?: string;
}

export function ReplayControls(props: ControlsProps) {
  return (
    <div className="replay-bar" role="toolbar" aria-label="Replay">
      <span className="replay-badge">Replay</span>
      <div className="chart-group" role="group" aria-label="Replay controls">
        <ChartChip
          onClick={props.onRestart}
          disabled={props.atStart}
          title="Back to the start"
          aria-label="Back to the start"
        >
          ⏮
        </ChartChip>
        <ChartChip
          onClick={() => props.onStep(-1)}
          disabled={props.atStart}
          title="One candle back"
          aria-label="One candle back"
        >
          ◀
        </ChartChip>
        <ChartChip
          className="replay-play"
          onClick={props.onPlay}
          disabled={props.atEnd && !props.playing}
          aria-label={props.playing ? "Pause" : "Play"}
        >
          {props.playing ? "❚❚ Pause" : "▶ Play"}
        </ChartChip>
        <ChartChip
          onClick={() => props.onStep(1)}
          disabled={props.atEnd}
          title="One candle forward"
          aria-label="One candle forward"
        >
          ▶
        </ChartChip>
        <ChartChip
          onClick={props.onNextTrade}
          disabled={!props.hasNextTrade}
          title={props.nextTitle ?? "Jump to the next entry"}
        >
          {props.nextLabel ?? "Next trade"}
        </ChartChip>
      </div>
      <label className="chart-range">
        <span className="visually-hidden">Speed</span>
        <select
          value={props.speed}
          onChange={(e) => props.onSpeed(Number(e.target.value))}
          title="Candles per second"
        >
          {SPEEDS.map((speed) => (
            <option key={speed} value={speed}>
              {speed}×
            </option>
          ))}
        </select>
      </label>
      <span className="replay-time">{props.label}</span>
      <ChartChip className="chart-add replay-exit" onClick={props.onExit}>
        Exit replay
      </ChartChip>
    </div>
  );
}

/** Each case's results, so far and over the whole replay, with a box to show or hide it. */
function CaseResults({
  all,
  until,
  hidden,
  onToggle,
}: {
  all: ReplayData;
  until: number;
  hidden: Set<number>;
  onToggle: (index: number) => void;
}) {
  const cases = all.cases ?? [{ name: "Entry rules", bias: "neutral" }];
  const percent = (part: number, whole: number) =>
    whole ? `${Math.round((part / whole) * 100)}%` : "–";
  return (
    <section className="replay-section" aria-label="Results by case">
      <h3>Results by case</h3>
      <ul className="replay-case-list">
        {cases.map((item, index) => {
          const trades = all.trades.filter((t) => (t.case ?? 0) === index);
          const closed = trades.filter((t) => t.close_time < until);
          const won = (list: ReplayTrade[]) =>
            list.filter((t) => t.net_pnl > 0).length;
          const net = (list: ReplayTrade[]) =>
            list.reduce((sum, t) => sum + t.net_pnl, 0);
          return (
            <li
              key={index}
              className={hidden.has(index) ? "replay-case-off" : undefined}
            >
              <label className="replay-case">
                <input
                  type="checkbox"
                  checked={!hidden.has(index)}
                  aria-label={`Show ${item.name} trades`}
                  onChange={() => onToggle(index)}
                />
                <span className="replay-case-name">{item.name}</span>
                <span className="muted">{item.bias}</span>
              </label>
              <dl className="replay-case-stats">
                <div>
                  <dt>So far</dt>
                  <dd>
                    {closed.length ? <Pnl value={net(closed)} /> : "–"}{" "}
                    <span className="muted">
                      {closed.length} closed,{" "}
                      {percent(won(closed), closed.length)} won
                    </span>
                  </dd>
                </div>
                <div>
                  <dt>Whole replay</dt>
                  <dd>
                    {trades.length ? <Pnl value={net(trades)} /> : "–"}{" "}
                    <span className="muted">
                      {trades.length} trades,{" "}
                      {percent(won(trades), trades.length)} won
                    </span>
                  </dd>
                </div>
              </dl>
            </li>
          );
        })}
      </ul>
      <p className="muted replay-plan">
        Net is after costs. Untick a case to hide its trades from the chart and
        the totals. The backtest takes one trade at a time, so one case&apos;s
        trades can have blocked another&apos;s.
      </p>
    </section>
  );
}

/** A trade in the side panel. Clicking it opens the option's chart, its levels and why it
 * was taken. */
function TradeItem({
  trade,
  until,
  isOpen,
  openPnl,
  selected,
  onSelect,
  intradayTimes,
  decideEvery,
}: {
  trade: ReplayTrade;
  until: number;
  isOpen: boolean;
  openPnl: number;
  selected: boolean;
  onSelect: () => void;
  intradayTimes: boolean;
  decideEvery: string;
}) {
  const stop = isOpen
    ? stopAt(trade, until)
    : trade.stop_pnl === null
      ? null
      : { level: trade.stop_pnl, kind: "stop loss" };
  const single = trade.legs.length === 1 ? trade.legs[0] : null;
  const optionNow = isOpen && single ? legPrice(trade, openPnl) : null;
  const value = isOpen ? openPnl : trade.net_pnl;
  return (
    <li
      className={cx(
        "replay-trade",
        isOpen && "is-open",
        selected && "is-selected",
      )}
    >
      <button
        type="button"
        className="replay-trade-head"
        aria-expanded={selected}
        onClick={onSelect}
      >
        <span className="replay-trade-legs">
          {trade.legs.map((leg) => legText(leg, trade.lots)).join(", ")}
        </span>
        <span className="replay-trade-value">
          <Pnl value={value} />
        </span>
        <span className="replay-trade-when muted">
          {isOpen
            ? `Open since ${wallText(trade.open_time, intradayTimes)}${optionNow !== null ? `, option ${price(optionNow)} from ${price(single!.entry_price)}` : ""}`
            : `${wallText(trade.open_time, intradayTimes)} to ${wallText(trade.close_time, intradayTimes)}, ${EXIT_REASONS[trade.exit_reason ?? ""] ?? trade.exit_reason}`}
        </span>
        <span className="replay-trade-hint muted">
          {isOpen ? "value before costs" : "after costs"}
          {selected ? ", hide chart" : ", show chart"}
        </span>
      </button>
      {isOpen && stop && openPnl <= stop.level && (
        <p className="replay-past">
          Past the {stop.kind} by{" "}
          {signedInr(stop.level - openPnl).replace("+", "")}: it exits at the
          next check.
        </p>
      )}
      {selected && (
        <div className="replay-trade-body">
          <TradeChart trade={trade} until={until} />
          <LevelsTable
            trade={trade}
            stopPnl={stop?.level ?? null}
            stopLabel={
              stop && stop.kind !== "stop loss" ? stop.kind : "Stop loss"
            }
            stopPrice={single ? legPrice(trade, stop?.level) : null}
            targetPrice={single ? legPrice(trade, trade.target_pnl) : null}
          />
          <details className="replay-last" open>
            <summary>Why it entered</summary>
            <EntryReasons
              trade={trade}
              intradayTimes={intradayTimes}
              decideEvery={decideEvery}
            />
          </details>
        </div>
      )}
    </li>
  );
}

export function ReplayPanel({
  data,
  all,
  hiddenCases,
  onToggleCase,
  times,
  cursor,
  intradayTimes,
}: {
  data: ReplayData;
  /** Every trade, before unticked cases are taken out. */
  all: ReplayData;
  hiddenCases: Set<number>;
  onToggleCase: (index: number) => void;
  times: number[];
  cursor: number;
  intradayTimes: boolean;
}) {
  const { closed, open, openPnl, until } = stateAt(times, data, cursor);
  const [selected, setSelected] = useState<number | null>(null);
  const realised = closed.reduce((sum, trade) => sum + trade.net_pnl, 0);
  const wins = closed.filter((trade) => trade.net_pnl > 0).length;
  const recent = [...closed].reverse();
  const blocked = Object.entries(data.blocked).slice(0, 4);
  const toggle = (trade: ReplayTrade) =>
    setSelected((now) => (now === trade.open_time ? null : trade.open_time));
  const item = (trade: ReplayTrade, isOpen: boolean) => (
    <TradeItem
      key={`${trade.open_time}-${trade.close_time}`}
      trade={trade}
      until={until}
      isOpen={isOpen}
      openPnl={isOpen ? openPnl : 0}
      selected={selected === trade.open_time}
      onSelect={() => toggle(trade)}
      intradayTimes={intradayTimes}
      decideEvery={data.decide_every}
    />
  );

  return (
    <aside className="replay-panel" aria-label="Replay results">
      <section className="replay-summary" aria-label="Summary">
        <div>
          <span className="muted">Realised after costs</span>
          <strong>
            <Pnl value={realised} />
          </strong>
        </div>
        <div>
          <span className="muted">Closed</span>
          <strong>
            {closed.length}
            <span className="muted"> of {data.trades.length}</span>
          </strong>
        </div>
        <div>
          <span className="muted">Won / lost</span>
          <strong>
            {wins} / {closed.length - wins}
          </strong>
        </div>
      </section>

      <section className="replay-section" aria-label="Open trade">
        <h3>Open trade</h3>
        {open ? (
          <ul className="replay-trade-list">{item(open, true)}</ul>
        ) : (
          <p className="muted">
            {closed.length === data.trades.length && data.trades.length
              ? "Flat. Every trade of the replay is done."
              : "Flat, waiting for the entry rules to hold."}
          </p>
        )}
      </section>

      {(all.cases?.length ?? 1) > 1 && (
        <CaseResults
          all={all}
          until={until}
          hidden={hiddenCases}
          onToggle={onToggleCase}
        />
      )}

      <section className="replay-section" aria-label="Closed trades">
        <h3>
          Closed trades <span className="muted">latest first</span>
        </h3>
        {recent.length > 0 ? (
          <ul className="replay-trade-list">
            {recent.map((trade) => item(trade, false))}
          </ul>
        ) : data.trades.length === 0 ? (
          <div className="muted">
            <p>
              No trades between {data.start} and {data.end}.
            </p>
            {blocked.length > 0 && (
              <p>
                Most common reasons a day was skipped:{" "}
                {blocked
                  .map(([reason, count]) => `${reason} (${count})`)
                  .join("; ")}
                .
              </p>
            )}
          </div>
        ) : (
          <p className="muted">None closed yet.</p>
        )}
      </section>
      <p className="replay-note muted">
        The lab backtest of the strategy on {data.symbol}, {data.start} to{" "}
        {data.end}: the same fills, modelled spreads, costs and exits. Levels
        are the position&apos;s value before costs; exits fill at the next check
        {data.intraday ? `, every ${data.decide_every}` : ""}. Nothing is saved.
      </p>
    </aside>
  );
}

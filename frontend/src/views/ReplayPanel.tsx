import { ChartChip } from "../components/ui";
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

/** Why the entry rules held, and where the trade exits, as option prices. */
export function EntryWhy({
  trade,
  intradayTimes,
  decideEvery,
}: {
  trade: ReplayTrade;
  intradayTimes: boolean;
  decideEvery: string;
}) {
  const single = trade.legs.length === 1 ? trade.legs[0] : null;
  // A position-level P&L level as the single leg's option price.
  const legPrice = (pnl: number | null) => {
    if (!single || pnl === null) return null;
    const sign = single.side === "buy" ? 1 : -1;
    return single.entry_price + pnl / (single.units * sign);
  };
  const stopPrice = single?.stop_price ?? legPrice(trade.stop_pnl);
  const targetPrice = single?.target_price ?? legPrice(trade.target_pnl);
  return (
    <div className="replay-why">
      <h4>Why it entered</h4>
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
                {item.any_of ? "One of: " : ""}
                {item.rule}
              </span>
              <span className="muted">{readingText(item, intradayTimes)}</span>
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
      <LevelsTable
        trade={trade}
        stopPnl={trade.stop_pnl}
        stopPrice={stopPrice}
        targetPrice={targetPrice}
      />
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
          title="Jump to the next entry"
        >
          Next trade
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
  const rows = cases.map((item, index) => {
    const trades = all.trades.filter((trade) => (trade.case ?? 0) === index);
    const closed = trades.filter((trade) => trade.close_time < until);
    const sum = (list: ReplayTrade[]) =>
      list.reduce((total, trade) => total + trade.net_pnl, 0);
    return {
      index,
      item,
      trades,
      closed,
      wins: closed.filter((trade) => trade.net_pnl > 0).length,
      soFar: sum(closed),
      total: sum(trades),
      totalWins: trades.filter((trade) => trade.net_pnl > 0).length,
    };
  });
  return (
    <section className="replay-cases" aria-label="Results by case">
      <h3>Results by case</h3>
      <table className="replay-levels replay-levels-grid">
        <thead>
          <tr>
            <th className="left">Case</th>
            <th className="right">Closed</th>
            <th className="right">Won</th>
            <th className="right">Net so far</th>
            <th className="right">Whole replay</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.index}
              className={hidden.has(row.index) ? "replay-case-off" : undefined}
            >
              <td>
                <label className="replay-case">
                  <input
                    type="checkbox"
                    checked={!hidden.has(row.index)}
                    aria-label={`Show ${row.item.name} trades`}
                    onChange={() => onToggle(row.index)}
                  />
                  {row.item.name}
                  <span className="muted">{row.item.bias}</span>
                </label>
              </td>
              <td className="right">
                {row.closed.length} of {row.trades.length}
              </td>
              <td className="right">
                {row.closed.length
                  ? `${Math.round((row.wins / row.closed.length) * 100)}%`
                  : "–"}
              </td>
              <td className="right">
                {row.closed.length ? <Pnl value={row.soFar} /> : "–"}
              </td>
              <td className="right">
                {row.trades.length ? (
                  <>
                    <Pnl value={row.total} />{" "}
                    <span className="muted">
                      {row.trades.length} trades,{" "}
                      {Math.round((row.totalWins / row.trades.length) * 100)}%
                      won
                    </span>
                  </>
                ) : (
                  <span className="muted">no trades</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted replay-plan">
        Net is after costs. Untick a case to hide its trades from the chart, the
        P&amp;L line and the totals below. Trades still happened in the backtest
        order: one trade at a time, so a case&apos;s trades can block
        another&apos;s.
      </p>
    </section>
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
  const stop = open ? stopAt(open, until) : null;
  const realised = closed.reduce((sum, trade) => sum + trade.net_pnl, 0);
  const wins = closed.filter((trade) => trade.net_pnl > 0).length;
  const recent = [...closed].reverse().slice(0, 8);
  const blocked = Object.entries(data.blocked).slice(0, 4);

  return (
    <div className="replay-panel">
      {(all.cases?.length ?? 1) > 1 && (
        <CaseResults
          all={all}
          until={until}
          hidden={hiddenCases}
          onToggle={onToggleCase}
        />
      )}
      <section className="replay-position" aria-label="Position">
        <h3>Position</h3>
        {open ? (
          <>
            <p className="replay-open-head">
              Open since {wallText(open.open_time, intradayTimes)}. Value now{" "}
              <Pnl value={openPnl} />{" "}
              <span className="muted">before costs</span>
            </p>
            <table className="replay-legs">
              <tbody>
                {open.legs.map((leg, index) => {
                  // With one leg, the position's value gives the option's price now.
                  const sign = leg.side === "buy" ? 1 : -1;
                  const now =
                    open.legs.length === 1
                      ? leg.entry_price + openPnl / (leg.units * sign)
                      : null;
                  return (
                    <tr key={index}>
                      <td
                        className={
                          leg.side === "buy" ? "replay-buy" : "replay-sell"
                        }
                      >
                        {legText(leg, open.lots)}
                      </td>
                      <td className="right">
                        filled at {leg.entry_price.toFixed(2)}
                        {now !== null && <>, now {now.toFixed(2)}</>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <table className="replay-levels">
              <tbody>
                <tr>
                  <td>Exits at a profit of</td>
                  <td className="right">
                    <Pnl value={open.target_pnl} />
                  </td>
                </tr>
                <tr>
                  <td>
                    {stop
                      ? `Exits at the ${stop.kind}, a value of`
                      : "Stop loss"}
                  </td>
                  <td className="right">
                    {stop ? (
                      <Pnl value={stop.level} />
                    ) : (
                      <span className="muted">not recorded</span>
                    )}
                  </td>
                </tr>
                {stop &&
                  (openPnl > stop.level ? (
                    <tr>
                      <td className="muted">Room left before the stop</td>
                      <td className="right">
                        {signedInr(openPnl - stop.level).replace("+", "")}
                      </td>
                    </tr>
                  ) : (
                    <tr>
                      <td className="replay-past" colSpan={2}>
                        Past the {stop.kind} by{" "}
                        {signedInr(stop.level - openPnl).replace("+", "")}: it
                        exits at the next check.
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
            <EntryWhy
              trade={open}
              intradayTimes={intradayTimes}
              decideEvery={data.decide_every}
            />
            <p className="muted replay-plan">
              Levels are the position's value before costs, measured from the
              fill prices. Exits fill at the next check
              {data.intraday ? `, every ${data.decide_every}` : ""}, so the fill
              can be past the level.
            </p>
          </>
        ) : (
          <>
            <p className="muted">
              {closed.length === data.trades.length && data.trades.length
                ? "Flat. Every trade of the replay is done."
                : "Flat, waiting for the entry rules to hold."}
            </p>
            {closed.length > 0 && (
              <details className="replay-last">
                <summary>Last trade: why it entered</summary>
                <EntryWhy
                  trade={closed[closed.length - 1]}
                  intradayTimes={intradayTimes}
                  decideEvery={data.decide_every}
                />
              </details>
            )}
          </>
        )}
      </section>

      <section className="replay-trades" aria-label="Trades so far">
        <h3>
          Trades so far{" "}
          <span className="muted">
            {closed.length} of {data.trades.length}
          </span>
        </h3>
        {closed.length > 0 && (
          <p className="replay-total">
            Realised <Pnl value={realised} /> after costs, {wins} won,{" "}
            {closed.length - wins} lost
          </p>
        )}
        {recent.length > 0 ? (
          <ul>
            {recent.map((trade) => (
              <li key={`${trade.open_time}-${trade.close_time}`}>
                <span>
                  {wallText(trade.open_time, intradayTimes)} →{" "}
                  {wallText(trade.close_time, intradayTimes)}
                </span>
                <span className="muted">
                  {trade.legs.map((leg) => legText(leg, trade.lots)).join(", ")}
                </span>
                <span>
                  <Pnl value={trade.net_pnl} />{" "}
                  <span className="muted">
                    {EXIT_REASONS[trade.exit_reason ?? ""] ?? trade.exit_reason}
                    {trade.exit_reason === "stop" && trade.stop_pnl !== null
                      ? ` at ${signedInr(trade.stop_pnl)}`
                      : ""}
                    {trade.exit_reason === "target"
                      ? ` at ${signedInr(trade.target_pnl)}`
                      : ""}
                    {trade.legs.length === 1 &&
                    trade.legs[0].exit_price !== null
                      ? `, filled ${trade.legs[0].exit_price.toFixed(2)} from ${trade.legs[0].entry_price.toFixed(2)}`
                      : ""}
                  </span>
                </span>
              </li>
            ))}
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
        This is the lab backtest of the strategy on {data.symbol}, {data.start}{" "}
        to {data.end}: the same fills, modelled spreads, costs and exits.
        Nothing is saved.
      </p>
    </div>
  );
}

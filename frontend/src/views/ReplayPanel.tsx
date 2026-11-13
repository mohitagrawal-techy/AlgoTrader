import type { ReplayData } from "../api";
import { signedInr } from "../format";
import { legText, stateAt, wallText } from "./replay";

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
  return <b className={value >= 0 ? "pnl-up" : "pnl-down"}>{signedInr(value)}</b>;
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
        <button type="button" className="chart-chip" onClick={props.onRestart} disabled={props.atStart} title="Back to the start" aria-label="Back to the start">
          ⏮
        </button>
        <button type="button" className="chart-chip" onClick={() => props.onStep(-1)} disabled={props.atStart} title="One candle back" aria-label="One candle back">
          ◀
        </button>
        <button
          type="button"
          className="chart-chip replay-play"
          onClick={props.onPlay}
          disabled={props.atEnd && !props.playing}
          aria-label={props.playing ? "Pause" : "Play"}
        >
          {props.playing ? "❚❚ Pause" : "▶ Play"}
        </button>
        <button type="button" className="chart-chip" onClick={() => props.onStep(1)} disabled={props.atEnd} title="One candle forward" aria-label="One candle forward">
          ▶
        </button>
        <button type="button" className="chart-chip" onClick={props.onNextTrade} disabled={!props.hasNextTrade} title="Jump to the next entry">
          Next trade
        </button>
      </div>
      <label className="chart-range">
        <span className="visually-hidden">Speed</span>
        <select value={props.speed} onChange={(e) => props.onSpeed(Number(e.target.value))} title="Candles per second">
          {SPEEDS.map((speed) => (
            <option key={speed} value={speed}>
              {speed}×
            </option>
          ))}
        </select>
      </label>
      <span className="replay-time">{props.label}</span>
      <button type="button" className="chart-chip chart-add replay-exit" onClick={props.onExit}>
        Exit replay
      </button>
    </div>
  );
}

export function ReplayPanel({
  data,
  times,
  cursor,
  intradayTimes,
}: {
  data: ReplayData;
  times: number[];
  cursor: number;
  intradayTimes: boolean;
}) {
  const { closed, open, openPnl } = stateAt(times, data, cursor);
  const realised = closed.reduce((sum, trade) => sum + trade.net_pnl, 0);
  const wins = closed.filter((trade) => trade.net_pnl > 0).length;
  const recent = [...closed].reverse().slice(0, 8);
  const blocked = Object.entries(data.blocked).slice(0, 4);

  return (
    <div className="replay-panel">
      <section className="replay-position" aria-label="Position">
        <h3>Position</h3>
        {open ? (
          <>
            <p className="replay-open-head">
              Open since {wallText(open.open_time, intradayTimes)}. Value now <Pnl value={openPnl} />{" "}
              <span className="muted">before costs</span>
            </p>
            <table className="replay-legs">
              <tbody>
                {open.legs.map((leg, index) => (
                  <tr key={index}>
                    <td className={leg.side === "buy" ? "replay-buy" : "replay-sell"}>{legText(leg, open.lots)}</td>
                    <td className="right">filled at {leg.entry_price.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted replay-plan">
              Plans to exit at <Pnl value={open.target_pnl} /> profit or <Pnl value={-Math.abs(open.planned_loss)} /> loss, or by its other exit rules.
            </p>
          </>
        ) : (
          <p className="muted">
            {closed.length === data.trades.length && data.trades.length
              ? "Flat. Every trade of the replay is done."
              : "Flat, waiting for the entry rules to hold."}
          </p>
        )}
      </section>

      <section className="replay-trades" aria-label="Trades so far">
        <h3>
          Trades so far <span className="muted">{closed.length} of {data.trades.length}</span>
        </h3>
        {closed.length > 0 && (
          <p className="replay-total">
            Realised <Pnl value={realised} /> after costs, {wins} won, {closed.length - wins} lost
          </p>
        )}
        {recent.length > 0 ? (
          <ul>
            {recent.map((trade) => (
              <li key={`${trade.open_time}-${trade.close_time}`}>
                <span>
                  {wallText(trade.open_time, intradayTimes)} → {wallText(trade.close_time, intradayTimes)}
                </span>
                <span className="muted">{trade.legs.map((leg) => legText(leg, trade.lots)).join(", ")}</span>
                <span>
                  <Pnl value={trade.net_pnl} />{" "}
                  <span className="muted">{EXIT_REASONS[trade.exit_reason ?? ""] ?? trade.exit_reason}</span>
                </span>
              </li>
            ))}
          </ul>
        ) : data.trades.length === 0 ? (
          <div className="muted">
            <p>No trades between {data.start} and {data.end}.</p>
            {blocked.length > 0 && (
              <p>
                Most common reasons a day was skipped:{" "}
                {blocked.map(([reason, count]) => `${reason} (${count})`).join("; ")}.
              </p>
            )}
          </div>
        ) : (
          <p className="muted">None closed yet.</p>
        )}
      </section>
      <p className="replay-note muted">
        This is the lab backtest of the strategy on {data.symbol}, {data.start} to {data.end}: the same fills,
        modelled spreads, costs and exits. Nothing is saved.
      </p>
    </div>
  );
}

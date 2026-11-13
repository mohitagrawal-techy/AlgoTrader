import type { ReplayData, ReplayTrade } from "../api";

/** The last candle starting at or before `t`, or -1 when `t` is before every candle. */
export function indexAt(times: number[], t: number): number {
  let low = 0;
  let high = times.length - 1;
  let found = -1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (times[mid] <= t) {
      found = mid;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  return found;
}

export interface ReplayTimeline {
  /** Realised P&L after costs plus the open position's value, as each candle closes. */
  pnl: number[];
  /** Candle of each trade's entry and exit, or -1 before the first candle. */
  entries: number[];
  exits: number[];
  /** Where the open trade's stop and target sit on the same P&L scale, or null when flat. */
  stop: (number | null)[];
  target: (number | null)[];
}

/** The stop in force before `until`: the fixed stop, raised by move-to-cost and the trailing
 * stop once the trade has been in profit, as the paper engine does. */
export function stopAt(trade: ReplayTrade, until: number): { level: number; kind: string } | null {
  if (trade.stop_pnl === null || trade.stop_pnl === undefined) return null;
  let peak = 0;
  for (const point of trade.path) {
    if (point.time >= until) break;
    peak = Math.max(peak, point.pnl);
  }
  let level = trade.stop_pnl;
  let kind = "stop loss";
  if (trade.breakeven_pnl !== null && trade.breakeven_pnl !== undefined && peak >= trade.breakeven_pnl && level < 0) {
    level = 0;
    kind = "stop at cost";
  }
  if (trade.trail_pnl !== null && trade.trail_pnl !== undefined && peak > 0 && peak - trade.trail_pnl > level) {
    level = peak - trade.trail_pnl;
    kind = "trailing stop";
  }
  return { level, kind };
}

/** Lines the backtest's trades up with the chart's candles. A candle runs until the next one
 * starts, so anything stamped before that is known when the candle closes. */
export function timeline(times: number[], data: ReplayData): ReplayTimeline {
  const trades = data.trades;
  // -1 when the chart does not reach back that far; such a marker is not drawn.
  const entries = trades.map((trade) => indexAt(times, trade.open_time));
  const exits = trades.map((trade) => indexAt(times, trade.close_time));
  const pnl = times.map((_, i) => {
    const until = i + 1 < times.length ? times[i + 1] : Number.POSITIVE_INFINITY;
    let total = 0;
    for (const trade of trades) {
      if (trade.close_time < until) {
        total += trade.net_pnl;
      } else if (trade.open_time < until) {
        total += openValue(trade, until);
      }
    }
    return Math.round(total * 100) / 100;
  });
  const stop: (number | null)[] = [];
  const target: (number | null)[] = [];
  times.forEach((start, i) => {
    const until = i + 1 < times.length ? times[i + 1] : Number.POSITIVE_INFINITY;
    // Open at any time during this candle, including the candle it exits in.
    const open = trades.find((trade) => trade.open_time < until && trade.close_time >= start);
    if (!open) {
      stop.push(null);
      target.push(null);
      return;
    }
    const realised = trades
      .filter((trade) => trade !== open && trade.close_time < until)
      .reduce((sum, trade) => sum + trade.net_pnl, 0);
    const level = stopAt(open, until);
    stop.push(level === null ? null : realised + level.level);
    target.push(realised + open.target_pnl);
  });
  return { pnl, entries, exits, stop, target };
}

/** The open position's value before costs from the last price known before `until`. */
export function openValue(trade: ReplayTrade, until: number): number {
  let value = 0;
  for (const point of trade.path) {
    if (point.time >= until) break;
    value = point.pnl;
  }
  return value;
}

/** The trade open at a candle, if any, and the trades closed by then. */
export function stateAt(times: number[], data: ReplayData, cursor: number) {
  const until = cursor + 1 < times.length ? times[cursor + 1] : Number.POSITIVE_INFINITY;
  const closed = data.trades.filter((trade) => trade.close_time < until);
  const open = data.trades.find((trade) => trade.open_time < until && trade.close_time >= until) ?? null;
  return { until, closed, open, openPnl: open ? openValue(open, until) : 0 };
}

export function legText(leg: ReplayTrade["legs"][number], lots: number): string {
  const side = leg.side === "buy" ? "Buy" : "Sell";
  return `${side} ${lots > 1 ? `${lots} lots ` : ""}${leg.strike.toLocaleString("en-IN")} ${leg.option_type} ${shortExpiry(leg.expiry)}`;
}

function shortExpiry(iso: string): string {
  const day = new Date(`${iso}T00:00:00+05:30`);
  return day.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });
}

/** Wall-clock seconds as shown on the chart, for labels. */
export function wallText(seconds: number, withTime: boolean): string {
  const date = new Date(seconds * 1000);
  return date.toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    year: withTime ? undefined : "numeric",
    hour: withTime ? "2-digit" : undefined,
    minute: withTime ? "2-digit" : undefined,
    hour12: false,
    timeZone: "UTC",
  });
}

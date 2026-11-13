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
  return { pnl, entries, exits };
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

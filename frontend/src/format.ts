const DASH = "–";
const IST = "Asia/Kolkata";

type Value = number | null | undefined;

const missing = (value: Value): value is null | undefined =>
  value === null || value === undefined || Number.isNaN(value);

export function inr(value: Value, digits = 0): string {
  if (missing(value)) return DASH;
  return value.toLocaleString("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

export function signedInr(value: Value, digits = 0): string {
  if (missing(value)) return DASH;
  return `${value > 0 ? "+" : ""}${inr(value, digits)}`;
}

export function inrCompact(value: number): string {
  const size = Math.abs(value);
  const sign = value < 0 ? "−" : "";
  if (size >= 1e7) return `${sign}₹${trim(size / 1e7)}Cr`;
  if (size >= 1e5) return `${sign}₹${trim(size / 1e5)}L`;
  if (size >= 1e3) return `${sign}₹${trim(size / 1e3)}K`;
  return `${sign}₹${trim(size)}`;
}

function trim(value: number): string {
  return value >= 100
    ? Math.round(value).toLocaleString("en-IN")
    : String(Number(value.toFixed(1)));
}

/** Rounds first, so a tiny negative value never displays as -0.00. */
function rounded(value: number, digits: number): number {
  const result = Number(value.toFixed(digits));
  return result === 0 ? 0 : result;
}

export function num(value: Value, digits = 0): string {
  if (missing(value)) return DASH;
  return rounded(value, digits).toLocaleString("en-IN", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

export function compact(value: Value): string {
  if (missing(value)) return DASH;
  const size = Math.abs(value);
  const sign = value < 0 ? "−" : "";
  if (size >= 1e7) return `${sign}${trim(size / 1e7)}Cr`;
  if (size >= 1e5) return `${sign}${trim(size / 1e5)}L`;
  if (size >= 1e3) return `${sign}${trim(size / 1e3)}K`;
  return `${sign}${trim(size)}`;
}

export function pct(value: Value, digits = 1): string {
  return missing(value) ? DASH : `${rounded(value, digits).toFixed(digits)}%`;
}

/** Formats a decimal fraction such as 0.142 as a percentage. */
export function fraction(value: Value, digits = 1): string {
  return missing(value) ? DASH : `${(value * 100).toFixed(digits)}%`;
}

export function shortDate(iso: string): string {
  return new Date(`${iso}T00:00:00+05:30`).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    timeZone: IST,
  });
}

export function timeIst(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: IST,
  });
}

export function dateTimeIst(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: IST,
  });
}

export function age(iso: string | null, now: number): string {
  if (!iso) return "no data yet";
  const minutes = Math.max(0, Math.round((now - new Date(iso).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours} hr ago` : `${Math.round(hours / 24)} days ago`;
}

export const STRATEGY_LABELS: Record<string, string> = {
  long_call: "Long call",
  long_put: "Long put",
  bull_call_debit: "Bull call spread",
  bear_put_debit: "Bear put spread",
  bull_put_credit: "Bull put credit",
  bear_call_credit: "Bear call credit",
  iron_condor: "Iron condor",
  call_butterfly: "Call butterfly",
  short_straddle: "Short straddle",
  short_strangle: "Short strangle",
  long_straddle: "Long straddle",
  long_strangle: "Long strangle",
  call_calendar: "Call calendar",
};

export const PROFILE_LABELS: Record<string, string> = {
  directional_buy: "Directional",
  debit_spread: "Debit spreads",
  credit_spread: "Credit spreads",
  butterfly: "Butterflies",
  short_premium: "Short premium",
  long_vol: "Long volatility",
  calendar: "Calendars",
};

export const GROUP_LABELS: Record<string, string> = {
  regime: "Market regime",
  volatility: "Volatility",
  positioning: "Positioning",
  levels: "Levels",
  liquidity: "Liquidity",
  structure: "Structure",
  costs: "Costs",
  events: "Events",
};

export const PARAM_LABELS: Record<string, string> = {
  trend_alignment: "Trend alignment",
  trend_strength: "Trend strength (ADX)",
  vix_level: "India VIX level",
  vix_change: "India VIX 5-day change",
  rv_percentile: "Realised vol percentile",
  session_calm: "Calm last session",
  iv_rank: "IV rank",
  iv_percentile: "IV percentile",
  iv_rv_spread: "IV minus realised vol",
  term_slope: "Term structure slope",
  skew_25d: "25-delta skew",
  pcr_alignment: "Put-call ratio fit",
  oi_buildup_alignment: "OI buildup fit",
  wall_room: "Room to OI wall",
  max_pain_distance: "Max pain fit",
  level_room: "Room to next level",
  support_proximity: "Nearby level support",
  breakeven_vs_zones: "Breakevens beyond levels",
  spread_quality: "Bid-ask quality",
  leg_volume: "Weakest leg volume",
  leg_oi: "Weakest leg open interest",
  strike_distance: "Strike distance",
  pop: "Probability of profit",
  ev_ratio: "Expected value per risk",
  reward_risk: "Reward to risk",
  breakeven_distance: "Breakeven distance",
  theta_gamma: "Theta to gamma",
  delta_neutrality: "Delta neutrality",
  return_on_margin: "Return on margin",
  cost_efficiency: "Cost efficiency",
  event_clear: "Event calendar clear",
  expiry_proximity: "Time to expiry",
};

export const EXIT_LABELS: Record<string, string> = {
  target: "Target",
  stop: "Stop",
  time: "Time exit",
  daily_loss_halt: "Daily loss halt",
  expiry_settlement: "Expiry settlement",
};

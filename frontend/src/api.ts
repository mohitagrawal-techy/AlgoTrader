/** How far a long job's current stage has got, in sessions or replays. */
export interface JobProgressInfo {
  done: number;
  total: number;
  unit: "sessions" | "replays";
  stage_started_at: string;
  updated_at: string;
}

export interface JobInfo {
  kind: string;
  state: "running" | "done" | "failed";
  message: string;
  started_at?: string;
  finished_at?: string | null;
  progress?: JobProgressInfo | null;
}

export interface Health {
  upstox_configured: boolean;
  broker_session_valid: boolean;
  broker_session_expires_at: string | null;
  market_open: boolean;
  data_as_of: string | null;
  server_time: string;
}

export interface IdeaLeg {
  side: number;
  ratio: number;
  strike: number;
  option_type: "CE" | "PE";
  expiry: string;
  lot_size: number;
  bid: number;
  ask: number;
  iv: number;
  delta: number;
  oi: number;
  volume: number;
}

export interface ScorePart {
  name: string;
  group: string;
  raw: number | null;
  score: number | null;
  adjusted: number | null;
  weight: number;
  prefer: "high" | "low" | "mid";
  note: string;
}

export interface Structure {
  dte: number;
  entry_debit: number;
  max_loss: number;
  max_profit: number;
  bounded: boolean;
  breakevens: number[];
  reward: number;
  reward_risk: number | null;
  pop: number;
  ev: number;
  net_ev: number;
  round_trip_cost: number;
  exit_spread: number;
  margin: number;
  net_delta: number;
  net_gamma: number;
  net_theta: number;
  net_vega: number;
  expected_move: number;
}

export interface AiReview {
  status: "ok" | "unavailable" | "over_limit";
  model: string;
  thesis: string;
  bear_case: string;
  adjustment: number;
  veto: boolean;
  veto_reason: string | null;
  cached: boolean;
}

export interface ResearchBrief {
  generated_at: string;
  model: string;
  summary: string;
  sentiment: "bullish" | "bearish" | "neutral";
  catalysts: string[];
  risks: string[];
  events: { name: string; date: string | null; impact: string; in_calendar?: boolean }[];
  sources: { title: string; link: string; published: string | null }[];
}

export interface Idea {
  id: string;
  base_score: number | null;
  ai: AiReview | null;
  symbol: string;
  strategy: string;
  profile: string;
  bias: string;
  front_expiry: string;
  tradable: boolean;
  score: number | null;
  coverage: number;
  rejections: string[];
  lots: number;
  spot: number;
  legs: IdeaLeg[];
  structure: Structure;
  plan: { target_pnl: number; stop_pnl: number; exit_dte: number; planned_loss: number };
  stress: { worst_pnl: number; spot_move_pct: number; iv_shift_pct: number };
  parts: ScorePart[];
  payoff: [number, number][];
}

export interface IdeasPayload {
  generated_at: string | null;
  ideas: Idea[];
}

export interface QuoteSide {
  bid: number | null;
  ask: number | null;
  ltp: number | null;
  iv: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  oi: number | null;
  oi_change: number | null;
  volume: number | null;
}

export interface ChainRow {
  strike: number;
  ce: QuoteSide | null;
  pe: QuoteSide | null;
}

export interface Zone {
  center: number;
  low: number;
  high: number;
  methods: string[];
  strength: number;
}

export interface ExpiryMarket {
  expiry: string;
  dte: number;
  forward: number;
  atm_iv: number | null;
  expected_move: number | null;
  skew: number | null;
  pcr: number | null;
  max_pain: number | null;
  buildup: number | null;
  walls: { resistance: number[]; support: number[] };
  zones: Zone[];
  rows: ChainRow[];
}

export interface LiveSide {
  bid: number | null;
  ask: number | null;
  ltp: number | null;
  iv: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  oi: number | null;
  volume: number | null;
}

export interface StrikePoint {
  /** Milliseconds since the epoch. */
  t: number;
  ltp: number | null;
  oi: number | null;
  /** A fraction, 0.15 for 15%. */
  iv: number | null;
  volume: number | null;
  bid: number | null;
  ask: number | null;
  delta: number | null;
  gamma: number | null;
  /** Rupees per unit per day. */
  theta: number | null;
  /** Rupees per unit per volatility point. */
  vega: number | null;
}

export type Buildup = "long_buildup" | "short_buildup" | "short_covering" | "long_unwinding" | "neutral";

export interface BuildupStep {
  /** Start of the block, in milliseconds since the epoch. */
  t: number;
  price_change: number;
  oi_change: number;
  label: Buildup;
}

export interface UnderlyingPoint {
  t: number;
  spot: number | null;
  /** The nearest future's VWAP, moved onto spot by the futures premium. */
  vwap: number | null;
  ema_fast: number | null;
  ema_slow: number | null;
  rsi: number | null;
}

export interface UnderlyingView {
  symbol: string;
  day: string | null;
  days: string[];
  source: "ticks" | "snapshots" | null;
  /** 1 from the live feed, 5 from chain snapshots. Indicators use this interval. */
  interval_minutes: number | null;
  points: UnderlyingPoint[];
  /** day_high, day_low, day_open, or_high, or_low, prev_high, prev_low, prev_close, pivot_p … */
  levels: Record<string, number>;
  previous: { day: string; high: number; low: number; close: number } | null;
  pcr: { t: number; pcr: number }[];
  vix: { t: number; vix: number }[];
}

export interface StrikeHistory {
  symbol: string;
  expiry: string;
  strike: number;
  option_type: "CE" | "PE";
  day: string | null;
  /** Days with saved chains, newest first. */
  days: string[];
  instrument_key: string | null;
  /** "ticks" is one point a minute from the live feed, "snapshots" one every 5 minutes. */
  source: "ticks" | "snapshots" | null;
  points: StrikePoint[];
  /** Every strike saved for this expiry on the day. */
  strikes: number[];
  /** Open-interest build-up in each 15-minute block, from the change in price and OI. */
  buildup: BuildupStep[];
}

export interface LiveChain {
  symbol: string;
  expiry: string;
  state: string;
  live: boolean;
  as_of?: string | null;
  spot?: number | null;
  rows: { strike: number; ce?: LiveSide; pe?: LiveSide }[];
}

export interface SymbolMarket {
  symbol: string;
  spot: number;
  iv_rank: number | null;
  iv_percentile: number | null;
  iv_sample_size: number | null;
  iv_source: string;
  realised_vol: number | null;
  vix_change_pct: number | null;
  trend: string | null;
  adx: number | null;
  term_slope: number | null;
  term_structure: { expiry: string; dte: number; atm_iv: number }[];
  expiries: ExpiryMarket[];
  research: ResearchBrief | null;
}

export interface MarketPayload {
  generated_at: string | null;
  symbols: Record<string, SymbolMarket>;
}

export interface Position {
  id: number;
  symbol: string;
  strategy: string;
  status: string;
  lots: number;
  created_at: string;
  opened_at: string | null;
  closed_at: string | null;
  target_pnl: number;
  stop_pnl: number;
  planned_loss: number;
  margin: number;
  score: number;
  entry_costs: number;
  exit_costs: number;
  exit_reason: string | null;
  realized_pnl: number | null;
  unrealized_pnl: number;
  stale: boolean;
  settlement_estimated: boolean;
  front_expiry: string;
  legs: {
    strike: number;
    option_type: string;
    side: number;
    units: number;
    expiry: string;
    entry_price: number | null;
    mark: number | null;
    exit_price: number | null;
  }[];
}

export interface Limit {
  key: string;
  label: string;
  used: number;
  limit: number;
  unit: "money" | "count";
}

/** Planned risk, net Greeks and open P&L for one underlying. Gamma is signed. */
export interface SymbolExposure {
  symbol: string;
  positions: number;
  planned_risk: number;
  limit: number;
  delta_notional: number;
  vega: number;
  gamma_1pct: number;
  open_pnl: number;
}

/** Instant P&L of the open book: rows follow `spot_moves_pct`, columns `iv_shifts_pct`. */
export interface StressGrid {
  spot_moves_pct: number[];
  iv_shifts_pct: number[];
  pnl: number[][];
  limit: number;
  approximated_legs: number;
}

export interface Portfolio {
  capital: number;
  realized: number;
  unrealized: number;
  day_pnl: number;
  halted: boolean;
  auto_trade: boolean;
  min_score: number;
  limits: Limit[];
  live: Position[];
  by_symbol: SymbolExposure[];
  stress: StressGrid;
  closed: Position[];
  closed_total: number;
  wins: number;
  unfilled_total: number;
  equity: { ts: string; pnl: number }[];
  activity: { ts: string; position_id: number | null; kind: string; detail: string }[];
}

export interface TradeStats {
  trades?: number;
  net_pnl?: number;
  costs?: number;
  win_rate?: number;
  average_win?: number;
  average_loss?: number;
  expectancy?: number;
  profit_factor?: number | null;
  longest_losing_streak?: number;
  average_holding_days?: number;
  tail_loss?: number;
  annual_return?: number | null;
  sharpe?: number | null;
  sortino?: number | null;
  max_drawdown?: number;
  worst_day?: number;
  negative_months_pct?: number;
}

export interface BreakdownRow extends TradeStats {
  group: string;
  trades: number;
  net_pnl: number;
}

export interface BacktestFold {
  train: [string, string];
  test: [string, string];
  chosen: { min_score: number; disabled_profiles: string[] } | null;
  train_sharpe: number | null;
  test_sharpe?: number | null;
  test_net_pnl?: number;
  test_trades?: number;
  sensitivity?: { sharp_peak: boolean };
}

export interface BacktestReport {
  id: string;
  kind: string;
  created_at: string;
  verdict: string | null;
  window: { start?: string; end?: string; sessions?: number };
  trials_to_date: number;
  grid_size?: number;
  folds?: BacktestFold[];
  double_slippage?: { net_pnl: number; sharpe: number | null };
  summary: {
    overall: TradeStats;
    breakdowns: Record<string, BreakdownRow[]>;
    monte_carlo: { drawdown_p50: number; drawdown_p95: number } | null;
    stress_windows: { name: string; covered: boolean; net_pnl?: number }[];
    warnings: string[];
    equity: { day: string; pnl: number }[];
  };
}

export interface BacktestJob extends JobInfo {
  kind: "walk_forward" | "backtest";
  started_at: string;
  finished_at: string | null;
  report_id: string | null;
}

export interface BacktestOptions {
  walk_forward: {
    ready: boolean;
    folds: number;
    needed: number;
    settings: number;
    train_sessions: number;
    test_sessions: number;
    holdout_sessions: number;
  };
  single: { first: string | null; last: string | null; min_scores: number[]; profiles: string[] };
}

export interface BacktestListing {
  history: { sessions: number; start: string | null; end: string | null };
  reports: { id: string; kind: string; created_at: string; verdict: string | null }[];
  options?: BacktestOptions;
  job?: BacktestJob | null;
}

export interface DriftCheck {
  key: string;
  label: string;
  status: string;
  actual: number | null;
  low: number | null;
  high: number | null;
  unit: string;
}

export interface ShadowRow extends TradeStats {
  stage: string;
  trades: number;
  unfilled: number;
  following: number;
}

export interface ForwardRun {
  id: number;
  name: string;
  version: number;
  role: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  end_reason: string | null;
  promoted_at: string | null;
  min_score: number;
  disabled_profiles: string[];
  locked_inputs_current: boolean;
  weeks: number;
  stats: TradeStats;
  drawdown: number;
  gate_limits: { max_drawdown_pct: number };
  gates: { key: string; label: string; passed: boolean; required: string; actual: string }[];
  drift: DriftCheck[];
  slippage_ratio: number | null;
  fills: number;
  shadow: ShadowRow[];
  ai_value: {
    approved_trades: number;
    vetoed_trades: number;
    enough_data: boolean;
    benefit_per_trade: number | null;
  };
  equity: { ts: string; pnl: number }[];
  live_positions: number;
}

export interface ForwardOverview {
  runs: ForwardRun[];
}

export interface IndexRow {
  name: string;
  label: string;
  last: number;
  change: number;
  change_pct: number | null;
  high: number | null;
  low: number | null;
  range_position: number | null;
  vs_nifty: number | null;
  returns: Record<string, number>;
}

export interface MoverRow {
  symbol: string;
  name: string;
  industry: string;
  last: number;
  change_pct: number;
  value: number;
}

export interface HeatStock {
  symbol: string;
  name: string;
  change_pct: number;
  value: number;
}

export interface HeatGroup {
  industry: string;
  value: number;
  stocks: HeatStock[];
}

export interface Candle {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  ema20: number | null;
  ema50: number | null;
}

export interface OverviewHistory {
  name: string;
  label: string;
  candles: Candle[];
}

export interface OverviewIntraday {
  name: string;
  label: string;
  status: string;
  session: string | null;
  live?: boolean;
  updated_at?: string | null;
  previous_close: number | null;
  candles: Candle[];
}

export interface IntradaySeries {
  name: string;
  label: string;
  session: string;
  previous_close: number | null;
  times: string[];
  closes: number[];
}

export interface OverviewIntradayComparison {
  status: string;
  live?: boolean;
  updated_at?: string | null;
  session: string | null;
  series: IntradaySeries[];
  volatility: IntradaySeries | null;
}

export interface OverviewComparison {
  dates: string[];
  series: { label: string; closes: number[] }[];
  volatility: { dates: string[]; closes: number[] };
}

export interface InstitutionFlow {
  date: string;
  fii_net: number | null;
  dii_net: number | null;
}

export interface InstitutionSegment {
  label: string;
  date: string;
  buy: number;
  sell: number;
  net: number;
  oi_contracts: number | null;
  oi_change: number | null;
  long_pct: number | null;
  call_net: number | null;
  put_net: number | null;
}

export interface OverviewInstitutions {
  status: "ok" | "session_expired" | "unavailable";
  fetched_at: string | null;
  as_of: string | null;
  fii_net: number | null;
  dii_net: number | null;
  combined_net: number | null;
  fii_recent: number | null;
  dii_recent: number | null;
  daily: InstitutionFlow[];
  monthly: InstitutionFlow[];
  segments: InstitutionSegment[];
}

export interface IndexMember {
  symbol: string;
  name: string;
  industry: string;
  last: number;
  change_pct: number;
}

export interface IndexDetails {
  name: string;
  label: string;
  last: number | null;
  change: number | null;
  change_pct: number | null;
  previous_close: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  range_position: number | null;
  vs_nifty: number | null;
  returns: Record<string, number>;
  quote_day: string | null;
  five_days: {
    date: string;
    open: number;
    high: number;
    low: number;
    close: number;
    change_pct: number | null;
  }[];
  stats: {
    high_52w: number | null;
    low_52w: number | null;
    from_high_pct: number | null;
    from_low_pct: number | null;
    ema20: number | null;
    ema50: number | null;
    realised_vol: number | null;
    average_range_pct: number | null;
  };
  has_constituents: boolean;
  constituents: {
    count: number;
    priced: number;
    advancing: number;
    declining: number;
    unchanged: number;
    average_change: number | null;
    members: IndexMember[];
  } | null;
}

export interface ConstituentList {
  name: string;
  label: string;
  stocks: number;
}

export interface OverviewPayload {
  status: "live" | "closed" | "session_expired" | "unavailable";
  fetched_at: string | null;
  last_trade_at: string | null;
  indices: IndexRow[];
  volatility: IndexRow | null;
  sectors: IndexRow[];
  has_sector_list: boolean;
  universe_size: number;
  breadth: {
    stocks: number;
    advancing: number;
    declining: number;
    unchanged: number;
    average_change: number;
    up_over_2: number;
    down_over_2: number;
    value: number;
  } | null;
  industries: {
    industry: string;
    stocks: number;
    advancing: number;
    declining: number;
    average_change: number;
    value: number;
    best: { symbol: string; change_pct: number };
    worst: { symbol: string; change_pct: number };
  }[];
  movers: { gainers: MoverRow[]; losers: MoverRow[]; active: MoverRow[] };
  heatmap: HeatGroup[];
  distribution: { label: string; count: number }[];
  options: {
    symbol: string;
    trend: string | null;
    iv_rank: number | null;
    atm_iv: number | null;
    pcr: number | null;
    max_pain: number | null;
    expected_move: number | null;
    expiry: string | null;
  }[];
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

async function streamText(
  path: string,
  payload: unknown,
  onModel: (model: string) => void,
  onText: (piece: string) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-AlgoVerse-Client": "dashboard" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : "The AI request failed.");
  }
  onModel(response.headers.get("X-AlgoVerse-Model") ?? "");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    const piece = decoder.decode(value, { stream: true });
    if (piece) onText(piece);
  }
}

export interface LabLeg {
  action: "buy" | "sell";
  option_type: "CE" | "PE";
  ratio: number;
  select: "delta" | "atm_offset" | "premium";
  value: number;
  expiry: number;
  /** The leg's own stop loss and profit target; hitting one closes only this leg. */
  stop?: number | null;
  stop_unit?: LegUnit;
  target?: number | null;
  target_unit?: LegUnit;
}

/** A percent of the leg's premium, or option points. */
export type LegUnit = "pct" | "points";

export interface LabCondition {
  signal: string;
  op: "above" | "below";
  value: number;
}

export type TechTimeframe = "1m" | "5m" | "15m" | "1h" | "1d" | "1w" | "1mo";
export type DecideEvery = "day" | "5m" | "15m" | "1h";
export type ExitUnit = "premium" | "rupees" | "points";
export type CandleType = "normal" | "heikin_ashi";

export interface TechOperand {
  kind: "value" | "indicator";
  value?: number | null;
  name?: string | null;
  params?: Record<string, number>;
  field?: string | null;
  /** 0 is the latest candle. */
  bars_ago?: number;
  /** The candles this side is read on, when not the rule's own; bars_ago counts these. */
  timeframe?: TechTimeframe;
  candles?: CandleType;
}

export type RuleJoin = "and" | "or";

/** A condition on the underlying itself: a comparison or a candle pattern. */
export type BaseTechRule = Exclude<TechRule, { type: "signal" }>;

/** A named signal of the strategy, read as true or false. */
export interface NamedSignal {
  name: string;
  /** Its conditions, joined by and/or as entry rules are. */
  rules: BaseTechRule[];
}

export type TechRule =
  | {
      type: "signal";
      join?: RuleJoin;
      /** The strategy signal it reads, by name. */
      name: string;
      present: boolean;
      /** Filled in by the server from the strategy's signal of that name. */
      rules?: BaseTechRule[];
    }
  | {
      type: "compare";
      /** How it joins the rule before it in an entry list; "and" binds before "or". */
      join?: RuleJoin;
      timeframe: TechTimeframe;
      candles?: CandleType;
      left: TechOperand;
      op: CompareOp;
      right: TechOperand;
    }
  | {
      type: "pattern";
      join?: RuleJoin;
      timeframe: TechTimeframe;
      candles?: CandleType;
      pattern: string;
      within: number;
      present: boolean;
    };

export interface ChartStudyRequest {
  id: string;
  name: string;
  params: Record<string, number>;
}

export interface ChartStudy {
  id: string;
  name: string;
  label?: string;
  unit?: string;
  params?: Record<string, number>;
  error?: string;
  lines: { field: string | null; label: string; overlay: boolean; values: (number | null)[] }[];
}

export interface ChartData {
  symbol: string;
  timeframe: TechTimeframe;
  empty?: string;
  timeframe_label?: string;
  source?: string;
  first?: string;
  time: number[];
  open: (number | null)[];
  high: (number | null)[];
  low: (number | null)[];
  close: (number | null)[];
  studies: ChartStudy[];
  signals: {
    rules: { group: "all" | "any"; text: string; count: number }[];
    other_timeframes: string[];
    problems: string[];
    hits: number[];
    /** A strategy's further cases, each with its own hits. */
    cases?: {
      name: string;
      bias: "bullish" | "bearish" | "neutral";
      rules: { group: "all" | "any"; text: string; count: number }[];
      hits: number[];
    }[];
  };
}

/** How a rule compares its two sides. "equals" compares to 2 decimals. */
export type CompareOp =
  | "above"
  | "below"
  | "at_or_above"
  | "at_or_below"
  | "equals"
  | "crosses_above"
  | "crosses_below";

export interface EntryReading {
  rule: string;
  holds: boolean;
  /** Start of the candle the values came from, IST wall-clock seconds. */
  candle?: number | null;
  left?: number | null;
  right?: number | null;
  left_before?: number | null;
  right_before?: number | null;
  left_name?: string;
  right_name?: string;
  op?: string;
  value_text?: string | null;
  any_of?: boolean;
  /** How it joins the rule before it; with "or" a rule may not hold while the entry does. */
  join?: RuleJoin;
}

export interface ReplayTrade {
  symbol: string;
  opened_at: string;
  closed_at: string;
  /** IST wall-clock epoch seconds, like the chart's time axis. */
  open_time: number;
  close_time: number;
  lots: number;
  exit_reason: string | null;
  net_pnl: number;
  costs: number;
  planned_loss: number;
  target_pnl: number;
  /** Exit levels in rupees for the whole position, against its fill prices. */
  stop_pnl: number | null;
  trail_pnl: number | null;
  breakeven_pnl: number | null;
  /** A stepped trailing stop: the stop rises by trail_pnl for each full step of gain. */
  trail_step_pnl?: number | null;
  legs: {
    side: "buy" | "sell";
    option_type: string;
    strike: number;
    expiry: string;
    units: number;
    entry_price: number;
    exit_price: number | null;
    /** The leg's own stop and target as option prices, when the leg sets them. */
    stop_price?: number | null;
    target_price?: number | null;
    /** Delta when the leg filled. Missing on older backtests. */
    entry_delta?: number | null;
  }[];
  /** Which of the strategy's cases opened it: 0 is the main entry rules. */
  case?: number;
  /** The index price when the position filled. Missing on older backtests. */
  entry_spot?: number | null;
  /** When the entry rules held and the order was placed (IST wall-clock seconds). It fills
   * at the next check, `open_time`. Null for backtests from before this was recorded. */
  decide_time?: number | null;
  /** Why the entry rules held at the decision. Null for older backtests. */
  why?: EntryReading[] | null;
  /** The open position's value before costs, by IST wall-clock time. */
  path: { time: number; pnl: number }[];
}

/** One option contract's candles, for the replay's trade chart. */
export interface OptionCandles {
  interval: "5m" | "1d";
  /** Each candle's start, IST wall-clock seconds. */
  time: number[];
  open: number[];
  high: number[];
  low: number[];
  close: number[];
}

export interface ReplayData {
  symbol: string;
  start: string;
  end: string;
  intraday: boolean;
  decide_every: string;
  trades: ReplayTrade[];
  /** The strategy's ways in, indexed by each trade's `case`. */
  cases?: { name: string; bias: string }[];
  stats: { trades: number; net_pnl: number | null; win_rate: number | null; expectancy: number | null; max_drawdown: number | null };
  signals: number;
  blocked: Record<string, number>;
}

export interface HistoryPeriod {
  label: string;
  start: string;
  end: string;
  trades: number;
  net_pnl: number;
  sharpe: number | null;
  folds: number;
}

export interface HistoryRun {
  ts: string;
  kind: "lab" | "tune" | "ladder" | "walk_forward";
  kind_label: string;
  strategy_id: string | null;
  strategy_name: string;
  step: string | null;
  periods: HistoryPeriod[];
  settings_tried?: number;
  entry_id?: string;
  verdict?: string | null;
  exists: boolean;
  changed_since: boolean;
}

export interface TestHistory {
  summary: {
    runs: number;
    by_kind: { kind: string; label: string; runs: number }[];
    strategies: number;
    first: string | null;
    last: string | null;
  };
  total: number;
  runs: HistoryRun[];
  kinds: { value: string; label: string }[];
  test_period: { start: string; end: string; reads: number } | null;
}

export interface HistoryEntry {
  entry: {
    id: string;
    strategy_id: string;
    name: string;
    evaluated_at: string;
    signature: string;
    spec: LabSpec;
    verdict: string;
    windows: Record<string, string>;
    train: LabSummary;
    test: LabSummary;
    test_2x: { trades: number; net_pnl: number; sharpe: number | null };
    evidence: { floor: number; trials: number } | null;
  };
  current: LabSpec | null;
  same_rules: boolean;
  latest: { verdict: string; evaluated_at: string; test: LabSummary } | null;
  entry_text: string | null;
  legs_text: string | null;
}

export interface TechCatalog {
  timeframes: { value: TechTimeframe; label: string }[];
  candle_types?: { value: CandleType; label: string }[];
  operators: { value: string; label: string }[];
  /** Indicator types, such as "Momentum", and their members, in the order to list them. */
  indicator_groups?: { name: string; indicators: string[] }[];
  indicators: {
    name: string;
    label: string;
    unit: string;
    description: string;
    params: { name: string; label: string; default: number; min: number; max: number; whole: boolean }[];
    fields: { value: string; label: string }[];
    intraday_only?: boolean;
    overlay?: boolean;
    /** Its type, one of `indicator_groups`. */
    group?: string;
    /** How it is worked out, such as "EMA = C × k + EMA[1] × (1 − k)". */
    formula?: string;
  }[];
  patterns: { name: string; label: string; bias: "bullish" | "bearish" | "neutral"; description: string }[];
}

/** An indicator chosen for a strategy, with its settings; its rules pick from these. */
export interface IndicatorPick {
  name: string;
  params: Record<string, number>;
  /** The user's own name for it, such as "fast EMA". Empty shows its standard name. */
  label?: string | null;
  /** The candles it is read on. */
  timeframe?: TechTimeframe;
  candles?: CandleType;
}

/** Another way into a strategy: its own legs and indicator rules, sharing everything else. */
export interface LabCase {
  name: string;
  bias: "bullish" | "bearish" | "neutral";
  legs: LabLeg[];
  rules?: TechRule[];
  any_rules?: TechRule[];
  /** The case's own exit rules; null or missing uses the strategy's. */
  exit_rules?: TechRule[] | null;
}

export interface LabSpec {
  id?: string;
  name: string;
  rationale: string;
  source?: "template" | "user" | "ai" | "ladder";
  bias: "bullish" | "bearish" | "neutral";
  symbols: string[];
  legs: LabLeg[];
  /** More cases after the first; the first whose rules hold trades. */
  cases?: LabCase[];
  /** The indicators the rules of every case choose from. */
  indicators?: IndicatorPick[];
  /** Named signals the entry and exit conditions read as true or false. */
  signals?: NamedSignal[];
  /** The account the strategy is tested as. */
  budget?: {
    capital: number;
    risk_per_trade_pct: number;
    max_open: number;
    /** "budget" buys as many lots as `deploy_pct` of capital allows; "risk" sizes by risk. */
    sizing?: "budget" | "risk";
    deploy_pct?: number;
    /** "atr" scales each trade's budget down when the underlying's daily range widens. */
    vol_sizing?: "off" | "atr";
    vol_floor?: number;
    vol_cap?: number;
    /** Circuit breakers: losing trades in a row, or a fall in percent of capital, then a pause. */
    pause_after_losses?: number | null;
    pause_drawdown_pct?: number | null;
    pause_sessions?: number;
  };
  entry: {
    trend: ("up" | "down" | "range")[];
    iv_rank_min: number | null;
    iv_rank_max: number | null;
    dte_min: number;
    dte_max: number;
    conditions: LabCondition[];
    /** Indicator, candle-pattern and price-level rules, each on its own timeframe. */
    rules?: TechRule[];
    /** At least one of these must hold as well. */
    any_rules?: TechRule[];
    /** Intraday strategies: enter only between these times ("HH:MM"). */
    time_from?: string | null;
    time_to?: string | null;
    max_trades_per_day?: number | null;
    reenter_after_stop?: boolean;
    /** False: no new trade after a profit target the same day. */
    reenter_after_target?: boolean;
    /** Wait and trade: after the rules hold, wait for the premium to move this far. */
    wait_move?: number | null;
    wait_unit?: LegUnit;
    wait_direction?: "up" | "down";
  };
  exits: {
    /** Amounts in the unit beside each: a fraction of premium, rupees per lot, or points. */
    target_frac: number;
    stop_frac: number;
    target_unit?: ExitUnit;
    stop_unit?: ExitUnit;
    exit_dte: number;
    /** Intraday strategies: close by this time ("HH:MM") every day. */
    square_off?: string | null;
    trail?: number | null;
    trail_unit?: ExitUnit;
    /** With a step, the stop rises by `trail` for every full step of gain, same unit. */
    trail_step?: number | null;
    breakeven_at?: number | null;
    breakeven_unit?: ExitUnit;
    /** Exit when any of these holds. */
    rules?: TechRule[];
    /** Rupees: stop the strategy for the day at this loss or gain. */
    daily_stop?: number | null;
    daily_target?: number | null;
  };
  /** "day" decides once at the close; the others decide through the session. */
  decide_every?: DecideEvery;
  parent_id?: string | null;
  rung?: number;
  step?: string | null;
}

export interface LabSummary {
  trades: number;
  net_pnl: number;
  win_rate?: number | null;
  expectancy?: number | null;
  sharpe?: number | null;
  max_drawdown?: number | null;
  max_drawdown_pct?: number | null;
  sortino?: number | null;
  annual_return?: number | null;
  total_return_pct?: number | null;
  average_win?: number | null;
  average_loss?: number | null;
  risk_reward?: number | null;
  longest_winning_streak?: number | null;
  longest_losing_streak?: number | null;
  recovery_factor?: number | null;
  exposure_pct?: number | null;
  capital_use_pct?: number | null;
  profit_factor?: number | null;
  signals: number;
  blocked: Record<string, number>;
  unfilled: Record<string, number>;
}

export interface LabResult {
  verdict: string;
  evaluated_at: string;
  windows: { train_start: string; train_end: string; test_start: string; test_end: string };
  train: LabSummary;
  test: LabSummary;
  test_2x: { trades: number | null; net_pnl: number | null; sharpe: number | null };
  equity: { day: string; pnl: number }[];
  evidence: { floor: number; prob_positive: number; trials: number } | null;
  /** Only in the list: whether the backtest page has per-trade detail. */
  has_detail?: boolean;
  /** Only on the backtest page: every trade and day of each window. */
  detail?: Record<"train" | "test", LabWindowDetail>;
}

export interface LabTradeLeg {
  side: "buy" | "sell";
  option_type: "CE" | "PE";
  strike: number;
  expiry: string;
  units: number;
  entry_price: number | null;
  exit_price: number | null;
  /** The leg's own exit levels and how it closed, when it closed before the rest. */
  stop_price?: number | null;
  target_price?: number | null;
  exit_reason?: string | null;
  closed_at?: string | null;
}

export interface LabTrade {
  symbol: string;
  opened: string;
  closed: string;
  /** Exact fill times, ISO. */
  opened_at?: string;
  closed_at?: string;
  lots: number;
  exit_reason: string | null;
  net_pnl: number;
  costs: number;
  planned_loss: number;
  target_pnl: number;
  settlement_estimated: boolean;
  trend: string;
  iv_regime: string;
  legs: LabTradeLeg[];
}

export interface LabWindowDetail {
  /** Net P&L by session date. */
  daily: Record<string, number>;
  trades: LabTrade[];
}

export type ResearchStageKey = "reject" | "research" | "paper_trade" | "approved" | "live_monitoring";

export interface ResearchStageInfo {
  key: ResearchStageKey;
  label: string;
  meaning: string;
}

/** Where a strategy stands on the research ladder, and every check behind it. */
export interface ResearchStage {
  stage: ResearchStageKey | null;
  reason: string;
  checks: { key: string; label: string; passed: boolean; detail: string }[];
  folds: { start: string; end: string; sessions: number; net_pnl: number; trades: number }[];
  /** Sharpe of the unseen sessions, and the same adjusted for sample and trials run. */
  significance?: {
    sharpe: number;
    psr: number;
    dsr: number | null;
    luck_sharpe: number;
    trials: number;
    sessions: number;
  } | null;
}

export type RegimeGroup = "trend" | "volatility" | "move";

export interface RegimeRow {
  key: string;
  group: RegimeGroup;
  label: string;
  sessions: number;
  trades: number;
  net_pnl: number;
  win_rate: number | null;
  expectancy: number | null;
}

/** Trades split by the market regime of the session each was opened in. */
export interface RegimeBreakdown {
  rows: RegimeRow[];
  suits: string[];
  avoid: string[];
  unlabelled_trades: number;
  min_trades: number;
}

export interface SearchParam {
  path: string;
  label: string;
  kind: "int" | "float";
  low: number;
  high: number;
  step: number;
  current: number;
}

export type SearchMethod = "grid" | "random" | "bayesian";

export interface SearchSummary {
  id: string;
  root_id: string;
  chosen_id: string | null;
  method: SearchMethod;
  created_at: string;
  state: string;
  message?: string;
}

export interface SearchSpace {
  params: SearchParam[];
  methods: SearchMethod[];
  max_trials: number;
  searches: SearchSummary[];
}

export interface SearchCell {
  i: number;
  j: number;
  x: number;
  y: number | null;
  valid: boolean;
  sharpe: number | null;
  trades: number | null;
  net_pnl: number | null;
  score: number | null;
}

type SearchScores = { trades: number; net_pnl: number; sharpe: number | null; win_rate: number | null };

export interface SearchRecord extends SearchSummary {
  root_name: string;
  params: SearchParam[];
  trials: { number: number; values: Record<string, number>; valid: boolean; design?: SearchScores; score?: number }[];
  validated: { values: Record<string, number>; design: SearchScores; validate: SearchScores }[];
  chosen_values: Record<string, number> | null;
  chosen_label: string | null;
  sensitivity?: {
    x: SearchParam;
    y: SearchParam | null;
    xs: number[];
    ys: (number | null)[];
    cells: SearchCell[];
    center: { i: number; j: number };
    neighbour_share: number;
    robust: boolean;
  };
  final?: { verdict: string; test: LabSummary; test_2x: LabSummary };
  runs: number;
}

export interface DossierSection {
  number: number;
  title: string;
  source: "code" | "ai" | "mixed";
  text?: string;
  note?: string | null;
  items?: [string, string][];
  bullets?: string[];
  table?: { columns: string[]; rows: string[][] } | null;
}

type PairWindow = {
  sessions: number;
  trades?: number;
  total_return_pct?: number;
  sharpe?: number | null;
  max_drawdown_pct?: number;
  win_share?: number | null;
  time_in_market?: number;
  start?: string;
};

export interface PairStudy {
  names: string[];
  sessions: number;
  error?: string;
  start?: string;
  end?: string;
  hedge_ratio?: number;
  adf_t?: number | null;
  cointegrated_at?: string | null;
  half_life_sessions?: number | null;
  latest_z?: number | null;
  rules?: { lookback: number; z_window: number; entry_z: number; exit_z: number; cost_bps: number };
  train?: PairWindow;
  test?: PairWindow;
  equity?: { day: string; value: number }[];
  test_start?: string;
}

type MlWindow = {
  sessions: number;
  total_return_pct: number;
  sharpe: number | null;
  psr: number | null;
  max_drawdown_pct: number;
  hit_rate: number | null;
  time_in_market: number;
};

export interface MlStudy {
  symbol: string;
  run_at: string;
  horizon: number;
  features: string[];
  folds: { train_end: string; test_start: string; test_end: string; train_sessions: number }[];
  sessions_tested: number;
  start: string;
  end: string;
  ml: MlWindow & { accuracy: number; auc: number | null; log_loss: number; base_rate: number };
  rules: (MlWindow & { name: string })[];
  best_rule: string;
  prob_better_than_best: number;
  adopted: boolean;
  importance: { feature: string; importance: number }[];
  latest: { day: string; prob_up: number };
  rules_note: string;
  equity: { day: string; ml: number; best_rule: number }[];
}

type IvStudy = {
  points: { month: string; implied: number; trailing: number; following: number | null }[];
  sessions: number;
  start?: string;
  end?: string;
  mean_premium?: number | null;
  premium_positive_share?: number | null;
  latest?: { implied: number; trailing: number } | null;
  window?: number;
};

export interface ResearchPayload {
  symbol: string;
  symbols: string[];
  time_of_day: {
    rows: { slot: string; mean_pct: number; up_share: number; abs_pct: number; sessions: number }[];
    sessions: number;
    start: string | null;
    end: string | null;
  };
  vix_vs_rv: IvStudy | null;
  atm_iv_vs_rv: IvStudy | null;
  atm_iv_sessions: number;
  correlations: { names: string[]; matrix: number[][]; sessions: number; start?: string; end?: string };
  rolling_correlation: { other: string; points: { week: string; corr: number }[] } | null;
  gaps: {
    rows: {
      label: string;
      sessions: number;
      filled_share?: number;
      held_share?: number;
      mean_gap_pct?: number;
      median_fill_minutes?: number | null;
    }[];
    sessions: number;
    start?: string;
    end?: string;
  };
  pairs: PairStudy[];
  ml: MlStudy | null;
}

export interface Coverage {
  name: string;
  first: string | null;
  last: string | null;
  sessions: number;
}

export interface DataAction {
  key: string;
  label: string;
  description: string;
  takes_start: boolean;
  plan_only: boolean;
}

export interface DataJob extends JobInfo {
  label: string;
  log: string[];
}

export interface DataPayload {
  inventory: {
    daily: Coverage[];
    minutes: Coverage[];
    options_daily: Omit<Coverage, "name">;
    options_intraday: Coverage[];
    contracts_on: string | null;
    chain_days: number;
    chains_last: string | null;
    holidays_updated: string | null;
    actions: DataAction[];
  };
  job: DataJob | null;
  session_valid: boolean;
}

export type CalendarKind = "owner" | "config" | "ai" | "holiday" | "special" | "expiry";

export interface CalendarItem {
  date: string;
  kind: CalendarKind;
  name: string;
  severity: "high" | "medium" | null;
  symbols: string[];
  id?: string;
  note?: string;
}

export interface CalendarPayload {
  start: string;
  end: string;
  today: string;
  symbols: string[];
  items: CalendarItem[];
  reviewed_through: string | null;
  reviewed_sources: { config: string | null; dashboard: string | null };
}

export interface NewCalendarEvent {
  date: string;
  name: string;
  severity: "high" | "medium";
  symbols: string[];
  note: string;
}

export interface Dossier {
  sections: DossierSection[];
  notes_written_at: string | null;
  tested: boolean;
  ai: { enabled: boolean };
}

export interface LabBacktestPage {
  spec: LabSpec;
  legs_text: string;
  entry_text: string;
  undefined_risk: boolean;
  level: "simple" | "moderate" | "complex";
  capital: number;
  result: LabResult | null;
  stage: ResearchStage;
  stages: ResearchStageInfo[];
  regimes?: RegimeBreakdown | null;
  /** The parameter search that produced this strategy, if any. */
  search?: { id: string; method: SearchMethod; robust: boolean; neighbour_share: number; trials: number } | null;
  /** Daily bars of each underlying the strategy traded. */
  bars: Record<string, { day: string; open: number; high: number; low: number; close: number }[]>;
  technical?: TechCatalog;
  /** Underlyings with option history. */
  symbols?: string[];
  chart_symbols?: string[];
}

export interface LadderNode {
  id: string;
  name: string;
  parent_id: string | null;
  rung: number;
  step: string | null;
  kind: string | null;
  level: "simple" | "moderate" | "complex";
  legs: string;
  entry: string;
  design: LabSummary;
  validate: LabSummary;
  prob_better: number | null;
  accepted: boolean | null;
  reason: string | null;
}

export interface LadderReport {
  run_at: string;
  windows: Record<string, string | number>;
  trials: number;
  rungs: { rung: number; tried: LadderNode[] }[];
  winner_id: string | null;
  lineage: LadderNode[];
  final?: Pick<LabResult, "verdict" | "test" | "test_2x" | "evidence">;
  final_period_uses: number;
}

/** Trades, wins and net P&L for one weekday (0 is Monday) and IST entry hour (-1: none). */
export interface TimingCell {
  day: number;
  hour: number;
  trades: number;
  wins: number;
  pnl: number;
}

export interface TuneNode {
  id: string;
  parent_id: string | null;
  depth: number;
  spec: LabSpec;
  label: string;
  path: string | null;
  reason: string | null;
  level: "simple" | "moderate" | "complex";
  legs_text: string;
  entry_text: string;
  design: LabSummary;
  design_exits: Record<string, number>;
  validate: LabSummary;
  accepted: boolean | null;
  verdict: string | null;
  prob_better: number | null;
  /** Cumulative unseen-period P&L by day. */
  validate_curve?: { day: string; pnl: number }[];
  /** Results by weekday and entry hour; missing in sessions saved before they were kept. */
  design_timing?: TimingCell[];
  validate_timing?: TimingCell[];
}

export interface TuneRound {
  round: number;
  parent_id: string;
  analysis: string;
  source: "ai" | "built_in";
  skipped: string[];
  tried: string[];
  kept: string[];
  /** Built-in changes that filled the round when the AI model's fell short. */
  built_in_added?: number;
}

type FinalTest = Pick<LabResult, "verdict" | "test" | "test_2x">;

/** A setting of a strategy that tuning may change. */
export interface TuneParam {
  path: string;
  group: string;
  label: string;
  /** The current value as text. */
  value: string;
  /** "rule" allows any part of a rule; "add" adds a new item, which only the AI suggests. */
  kind: "int" | "number" | "other" | "rule" | "add";
  current: number | null;
  /** The rule this setting is part of; it is listed under that rule. */
  parent?: string;
  /** Its name when shown alone, with the rule it belongs to. */
  full?: string;
}

export interface TuneSession {
  /** The settings chosen to tune; null or missing tunes them all. */
  params?: string[] | null;
  id: string;
  root_id: string;
  root_name: string;
  created_at: string;
  updated_at?: string;
  nodes: Record<string, TuneNode>;
  rounds: TuneRound[];
  best_id: string;
  trials: number;
  final?: FinalTest & { id: string };
  root_final?: FinalTest | null;
  final_period_uses?: number;
}

export interface TuneOverview {
  ready: boolean;
  ai: { enabled: boolean; model: string };
  job: (JobInfo & { session_id?: string | null }) | null;
  sessions: {
    id: string;
    root_name: string;
    created_at: string;
    updated_at?: string;
    tested: number;
    kept: number;
    root_validate: LabSummary | null;
    best_validate: LabSummary | null;
    best_label: string | null;
    final: (FinalTest & { id: string }) | null;
  }[];
  strategies: { id: string; name: string; source: string }[];
  technical?: TechCatalog;
  signals?: { name: string; label: string }[];
}

export interface LabRow {
  spec: LabSpec;
  legs_text: string;
  undefined_risk: boolean;
  level: "simple" | "moderate" | "complex";
  entry_text: string;
  result: LabResult | null;
  stage: ResearchStage;
  /** Ways the legs contradict the structure the name promises. */
  structure_problems?: string[];
  /** Market regimes with enough trades where it made, or lost, money on average. */
  regimes?: { suits: string[]; avoid: string[] } | null;
}

export interface AutopilotCycle {
  number: number;
  started_at: string;
  finished_at?: string;
  designed: {
    id: string;
    name: string;
    verdict: string | null;
    train: { trades: number; net_pnl: number; win_rate?: number | null; sharpe?: number | null } | null;
  }[];
  dropped: number;
  picked: { id: string; name: string } | null;
  session_id: string | null;
  final: { id: string; verdict: string; test?: LabSummary } | null;
  kept: { id: string; name: string; verdict: string }[];
  archived: number;
  message: string | null;
}

export interface AutopilotState {
  started_at: string;
  finished_at: string | null;
  running: boolean;
  ended: string | null;
  settings: { cycles: number; hours: number; per_cycle: number; tune_rounds: number; guidance: string };
  cycles: AutopilotCycle[];
}

export interface AutopilotSettings {
  cycles: number;
  hours: number;
  per_cycle: number;
  tune_rounds: number;
  guidance: string;
}

export interface LabOverview {
  history: { sessions: number; needed: number; ready: boolean };
  min_test_trades: number;
  stages: ResearchStageInfo[];
  tested: number;
  trials_logged: number;
  ai: { enabled: boolean; model: string };
  defaults: { rounds: number; per_round: number };
  job: JobInfo | null;
  symbols: string[];
  signals: { name: string; label: string }[];
  technical: TechCatalog;
  ladder: LadderReport | null;
  ladder_ready: boolean;
  /** Indices the chart can show: tested underlyings, then reference ones such as Sensex. */
  chart_symbols?: string[];
  autopilot: AutopilotState | null;
  archived: number;
  strategies: LabRow[];
}

async function sendJson<T>(path: string, method: string, payload?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method,
    signal,
    headers: { "Content-Type": "application/json", "X-AlgoVerse-Client": "dashboard" },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(errorText(response.status, body?.detail));
  return body;
}

const FIELD_NAMES: Record<string, string> = {
  rounds: "Rounds",
  per_round: "Changes per round",
  guidance: "Guidance",
  start_id: "Strategy",
  node_id: "Version",
};

/** A readable message from an API error, including the server's field-by-field checks. */
function errorText(status: number, detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((item: { loc?: (string | number)[]; msg?: string }) => {
        const field = String(item.loc?.[item.loc.length - 1] ?? "");
        const name = FIELD_NAMES[field] ?? field.replace(/_/g, " ");
        const message = (item.msg ?? "is not valid")
          .replace(/^Input should be less than or equal to/, "must be at most")
          .replace(/^Input should be greater than or equal to/, "must be at least")
          .replace(/^Input should be /, "must be ");
        return name ? `${name}: ${message}` : message;
      })
      .join(". ");
  }
  if (status === 403) return "This action must come from the dashboard.";
  if (status === 409) return "Another job is already running. Wait for it to finish.";
  if (status >= 500) return "The server hit an error. Check the backend log.";
  return `The request failed (HTTP ${status}).`;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path} returned ${response.status}`);
  return response.json();
}

export const api = {
  health: () => getJson<Health>("/api/health"),
  ideas: () => getJson<IdeasPayload>("/api/ideas"),
  market: () => getJson<MarketPayload>("/api/market"),
  marketLive: (symbol: string, expiry: string) =>
    getJson<LiveChain>(
      `/api/market/live?symbol=${encodeURIComponent(symbol)}&expiry=${encodeURIComponent(expiry)}`,
    ),
  marketStrike: (symbol: string, expiry: string, strike: number, type: "CE" | "PE", day?: string) =>
    getJson<StrikeHistory>(
      `/api/market/strike?${new URLSearchParams({
        symbol,
        expiry,
        strike: String(strike),
        option_type: type,
        ...(day ? { day } : {}),
      })}`,
    ),
  marketUnderlying: (symbol: string, expiry: string, day?: string) =>
    getJson<UnderlyingView>(
      `/api/market/underlying?${new URLSearchParams({ symbol, expiry, ...(day ? { day } : {}) })}`,
    ),
  labBacktestPage: (id: string) =>
    getJson<LabBacktestPage>(`/api/lab/strategies/${encodeURIComponent(id)}/backtest`),
  portfolio: () => getJson<Portfolio>("/api/portfolio"),
  backtests: () => getJson<BacktestListing>("/api/backtests"),
  startWalkForward: () => sendJson<BacktestJob>("/api/backtests/walk-forward", "POST"),
  startBacktest: (body: { start: string; end: string; min_score: number; disabled_profiles: string[] }) =>
    sendJson<BacktestJob>("/api/backtests/run", "POST", body),
  forward: () => getJson<ForwardOverview>("/api/forward"),
  lab: () => getJson<LabOverview>("/api/lab"),
  labSave: (spec: LabSpec) => sendJson<LabSpec>("/api/lab/strategies", "POST", spec),
  labDelete: (id: string) =>
    sendJson<unknown>(`/api/lab/strategies/${encodeURIComponent(id)}`, "DELETE"),
  labBacktest: (ids: string[] | null) => sendJson<unknown>("/api/lab/backtest", "POST", { ids }),
  labLadder: (startId: string | null) =>
    sendJson<unknown>("/api/lab/ladder", "POST", { start_id: startId }),
  labTuning: () => getJson<TuneOverview>("/api/lab/tune"),
  labTuningSession: (id: string) => getJson<TuneSession>(`/api/lab/tune/${encodeURIComponent(id)}`),
  labTune: (body: {
    start_id?: string;
    session_id?: string;
    node_id?: string;
    rounds: number;
    per_round: number;
    guidance: string;
    use_ai: boolean;
    /** Setting paths to tune; omitted or null tunes them all. */
    params?: string[] | null;
  }) => sendJson<unknown>("/api/lab/tune", "POST", body),
  labTuneParams: (specId: string) =>
    getJson<{ params: TuneParam[] }>(`/api/lab/tune/params/${encodeURIComponent(specId)}`),
  labTuneSave: (sessionId: string, nodeId: string) =>
    sendJson<{ id: string }>(`/api/lab/tune/${encodeURIComponent(sessionId)}/save`, "POST", { node_id: nodeId }),
  labChart: (
    body: {
      symbol: string;
      timeframe: TechTimeframe;
      candles: CandleType;
      sessions?: number;
      studies: ChartStudyRequest[];
      rules: TechRule[];
      any_rules: TechRule[];
      cases?: { name: string; bias: string; rules: TechRule[]; any_rules: TechRule[] }[];
    },
    signal?: AbortSignal,
  ) => sendJson<ChartData>("/api/lab/chart", "POST", body, signal),
  testHistory: (params: { kind?: string; q?: string; limit?: number; offset?: number }) => {
    const query = new URLSearchParams();
    if (params.kind) query.set("kind", params.kind);
    if (params.q) query.set("q", params.q);
    query.set("limit", String(params.limit ?? 100));
    query.set("offset", String(params.offset ?? 0));
    return getJson<TestHistory>(`/api/lab/history?${query}`);
  },
  testHistoryEntry: (id: string) => getJson<HistoryEntry>(`/api/lab/history/${encodeURIComponent(id)}`),
  labBacktestReplay: (id: string, window: string, symbol: string) =>
    getJson<ReplayData>(
      `/api/lab/strategies/${encodeURIComponent(id)}/replay?window=${encodeURIComponent(window)}&symbol=${encodeURIComponent(symbol)}`,
    ),
  labReplay: (body: { spec: LabSpec; symbol: string; sessions: number }) =>
    sendJson<ReplayData>("/api/lab/replay", "POST", body),
  labReplayOption: (params: {
    symbol: string;
    expiry: string;
    strike: number;
    option_type: string;
    start: string;
    end: string;
  }) =>
    getJson<OptionCandles>(
      `/api/lab/replay/option?${new URLSearchParams({ ...params, strike: String(params.strike) })}`,
    ),
  labAutopilot: (body: AutopilotSettings) => sendJson<unknown>("/api/lab/autopilot", "POST", body),
  labAutopilotStop: () => sendJson<unknown>("/api/lab/autopilot/stop", "POST"),
  data: () => getJson<DataPayload>("/api/data"),
  dataJob: () => getJson<{ job: DataJob | null }>("/api/data/job"),
  dataStart: (action: string, start: string | null) =>
    sendJson<DataJob>("/api/data/jobs", "POST", { action, start }),
  calendar: (start: string, end: string) =>
    getJson<CalendarPayload>(`/api/calendar?${new URLSearchParams({ start, end })}`),
  calendarAdd: (event: NewCalendarEvent) =>
    sendJson<CalendarItem>("/api/calendar/events", "POST", event),
  calendarDelete: (id: string) =>
    sendJson<unknown>(`/api/calendar/events/${encodeURIComponent(id)}`, "DELETE"),
  calendarReviewed: (through: string | null) =>
    sendJson<{ reviewed_through: string | null }>("/api/calendar/reviewed", "PUT", { through }),
  research: (symbol: string) =>
    getJson<ResearchPayload>(`/api/research?symbol=${encodeURIComponent(symbol)}`),
  researchJob: () => getJson<{ job: JobInfo | null }>("/api/research/job"),
  researchMl: (symbol: string, horizon: number) =>
    sendJson<JobInfo>("/api/research/ml", "POST", { symbol, horizon }),
  labJob: () => getJson<{ job: (JobInfo & { kind?: string; search_id?: string; spec_id?: string }) | null }>("/api/lab/job"),
  labSearchSpace: (id: string) =>
    getJson<SearchSpace>(`/api/lab/strategies/${encodeURIComponent(id)}/search`),
  labSearch: (id: string) => getJson<SearchRecord>(`/api/lab/search/${encodeURIComponent(id)}`),
  labStartSearch: (body: { start_id: string; params: string[]; method: SearchMethod; max_trials: number }) =>
    sendJson<{ search_id: string }>("/api/lab/search", "POST", body),
  labDossier: (id: string) => getJson<Dossier>(`/api/lab/strategies/${encodeURIComponent(id)}/dossier`),
  labDossierNotes: (id: string) =>
    sendJson<unknown>(`/api/lab/strategies/${encodeURIComponent(id)}/dossier`, "POST"),
  labDossierUrl: (id: string) => `/api/lab/strategies/${encodeURIComponent(id)}/dossier.md`,
  labGenerate: (guidance: string) => sendJson<unknown>("/api/lab/generate", "POST", { guidance }),
  overview: () => getJson<OverviewPayload>("/api/overview"),
  explain: (
    target: { component: string; title: string; params?: Record<string, string> },
    onModel: (model: string) => void,
    onText: (piece: string) => void,
    signal: AbortSignal,
  ) =>
    streamText(
      "/api/assistant/explain",
      { component: target.component, title: target.title, params: target.params ?? {} },
      onModel,
      onText,
      signal,
    ),
  chat: (
    messages: ChatMessage[],
    onModel: (model: string) => void,
    onText: (piece: string) => void,
    signal: AbortSignal,
  ) => streamText("/api/assistant/chat", { messages }, onModel, onText, signal),
  overviewHistory: (name: string) =>
    getJson<OverviewHistory>(`/api/overview/history?name=${encodeURIComponent(name)}`),
  overviewComparison: () => getJson<OverviewComparison>("/api/overview/comparison"),
  overviewIntraday: (name: string) =>
    getJson<OverviewIntraday>(`/api/overview/intraday?name=${encodeURIComponent(name)}`),
  overviewIntradayComparison: () =>
    getJson<OverviewIntradayComparison>("/api/overview/intraday-comparison"),
  overviewInstitutions: () => getJson<OverviewInstitutions>("/api/overview/institutions"),
  overviewDetails: (name: string) =>
    getJson<IndexDetails>(`/api/overview/details?name=${encodeURIComponent(name)}`),
  constituentLists: () => getJson<{ indices: ConstituentList[] }>("/api/overview/constituents"),
  async uploadConstituents(name: string, file: File): Promise<{ stocks: number }> {
    const response = await fetch(`/api/overview/constituents?name=${encodeURIComponent(name)}`, {
      method: "POST",
      headers: { "X-AlgoVerse-Client": "dashboard", "Content-Type": "text/csv" },
      body: file,
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new Error(body?.detail ?? "Upload failed.");
    return body;
  },
  async uploadSectors(file: File): Promise<{ stocks: number }> {
    const response = await fetch("/api/overview/sectors", {
      method: "POST",
      headers: { "X-AlgoVerse-Client": "dashboard", "Content-Type": "text/csv" },
      body: file,
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new Error(body?.detail ?? "Upload failed.");
    return body;
  },
  backtest: (id: string) => getJson<BacktestReport>(`/api/backtests/${encodeURIComponent(id)}`),
  async refresh(): Promise<void> {
    const response = await fetch("/api/refresh", {
      method: "POST",
      headers: { "X-AlgoVerse-Client": "dashboard" },
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(body?.detail ?? `Refresh failed with status ${response.status}`);
    }
  },
};

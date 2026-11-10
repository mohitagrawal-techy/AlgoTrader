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

export interface BacktestListing {
  history: { sessions: number; start: string | null; end: string | null };
  reports: { id: string; kind: string; created_at: string; verdict: string | null }[];
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

export interface OverviewComparison {
  dates: string[];
  series: { label: string; closes: number[] }[];
  volatility: { dates: string[]; closes: number[] };
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
  select: "delta" | "atm_offset";
  value: number;
  expiry: number;
}

export interface LabSpec {
  id?: string;
  name: string;
  rationale: string;
  source?: "template" | "user" | "ai";
  bias: "bullish" | "bearish" | "neutral";
  symbols: string[];
  legs: LabLeg[];
  entry: {
    trend: ("up" | "down" | "range")[];
    iv_rank_min: number | null;
    iv_rank_max: number | null;
    dte_min: number;
    dte_max: number;
  };
  exits: { target_frac: number; stop_frac: number; exit_dte: number };
}

export interface LabSummary {
  trades: number;
  net_pnl: number;
  win_rate?: number | null;
  expectancy?: number | null;
  sharpe?: number | null;
  max_drawdown?: number | null;
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
}

export interface LabRow {
  spec: LabSpec;
  legs_text: string;
  undefined_risk: boolean;
  result: LabResult | null;
}

export interface LabOverview {
  history: { sessions: number; needed: number; ready: boolean };
  min_test_trades: number;
  tested: number;
  trials_logged: number;
  ai: { enabled: boolean; model: string };
  defaults: { rounds: number; per_round: number };
  job: { kind: string; state: "running" | "done" | "failed"; message: string } | null;
  strategies: LabRow[];
}

async function sendJson<T>(path: string, method: string, payload?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", "X-AlgoVerse-Client": "dashboard" },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(typeof body?.detail === "string" ? body.detail : "The request failed.");
  }
  return body;
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
  portfolio: () => getJson<Portfolio>("/api/portfolio"),
  backtests: () => getJson<BacktestListing>("/api/backtests"),
  forward: () => getJson<ForwardOverview>("/api/forward"),
  lab: () => getJson<LabOverview>("/api/lab"),
  labSave: (spec: LabSpec) => sendJson<LabSpec>("/api/lab/strategies", "POST", spec),
  labDelete: (id: string) =>
    sendJson<unknown>(`/api/lab/strategies/${encodeURIComponent(id)}`, "DELETE"),
  labBacktest: (ids: string[] | null) => sendJson<unknown>("/api/lab/backtest", "POST", { ids }),
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

import { Pencil } from "lucide-react";
import { TimingGrid, timingFromTrades } from "./TimingGrid";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type LabBacktestPage, type LabTrade } from "../api";
import {
  LineChart,
  TradeChart,
  ValueColumns,
  timeTicks,
  type ChartTrade,
} from "../charts";
import {
  dateTimeIst,
  fraction,
  inrCompact,
  num,
  shortDate,
  signedInr,
} from "../format";
import {
  Empty,
  Panel,
  Pnl,
  LabeledSelect,
  Tile,
  type Tone,
  Table,
  Tiles,
  BackLink,
  ButtonLink,
  PAGE_SIZES,
  Pager,
  pageOf,
  Segmented,
} from "../components/ui";
import { PnlHeatmap } from "./PnlHeatmap";
import { IndexChart } from "./IndexChart";
import {
  MetricsGrid,
  StageBadge,
  StageCard,
} from "../components/research/Stage";
import { RegimeTable } from "../components/research/Regimes";
import { SearchPanel } from "../components/research/Search";
import { DossierPanel } from "../components/research/Dossier";

const VERDICTS: Record<string, { tone: Tone; label: string }> = {
  held_up: { tone: "good", label: "Held up on unseen data" },
  unproven: { tone: "warning", label: "Profit could be luck" },
  fails_at_2x_slippage: { tone: "warning", label: "Fails at 2x slippage" },
  failed: { tone: "critical", label: "Lost on unseen data" },
  too_few_trades: { tone: "neutral", label: "Too few trades" },
};
const EXIT_REASONS: Record<string, string> = {
  target: "Profit target",
  stop: "Stop loss",
  time: "Time exit before expiry",
  expiry_settlement: "Held to expiry",
  daily_loss_halt: "Daily loss limit",
  end_of_test: "End of the test window",
  end_of_test_at_last_mark: "End of the test window",
  square_off: "Square-off time",
  exit_rule: "Exit rule",
  trailing_stop: "Trailing stop",
  stop_at_cost: "Stop at cost",
  daily_stop: "Daily stop loss",
  daily_target: "Daily take profit",
};
const WINDOWS = {
  test: "Test (unseen)",
  train: "Training",
  both: "Training and test",
} as const;
type WindowKey = keyof typeof WINDOWS;
const PERIODS = { day: "Daily", week: "Weekly", month: "Monthly" } as const;
const PERIOD_PLURAL = { day: "days", week: "weeks", month: "months" } as const;
type Period = keyof typeof PERIODS;

function monthLabel(key: string): string {
  return new Date(`${key}-01T00:00:00+05:30`).toLocaleDateString("en-IN", {
    month: "short",
    year: "2-digit",
    timeZone: "Asia/Kolkata",
  });
}

/** Monday of the week a date falls in, as YYYY-MM-DD. */
function weekOf(day: string): string {
  const date = new Date(`${day}T00:00:00Z`);
  const shift = (date.getUTCDay() + 6) % 7;
  date.setUTCDate(date.getUTCDate() - shift);
  return date.toISOString().slice(0, 10);
}

function periodKey(day: string, period: Period): string {
  return period === "day"
    ? day
    : period === "week"
      ? weekOf(day)
      : day.slice(0, 7);
}

function periodLabel(key: string, period: Period): string {
  return period === "month" ? monthLabel(key) : shortDate(key);
}

function legText(trade: LabTrade): string {
  return trade.legs
    .map(
      (leg) =>
        `${leg.side === "buy" ? "Buy" : "Sell"} ${num(leg.strike)} ${leg.option_type}`,
    )
    .join(", ");
}

function priceText(trade: LabTrade): string {
  return trade.legs
    .map(
      (leg) =>
        `${num(leg.strike)} ${leg.option_type} ${num(leg.entry_price, 2)} → ${num(leg.exit_price, 2)}`,
    )
    .join("; ");
}

/** Periods newest first, each with the running total up to it. */
function runningRows<T extends { value: number }>(
  periods: T[],
): (T & { total: number })[] {
  let total = 0;
  return periods.map((p) => ({ ...p, total: (total += p.value) })).reverse();
}

/** "7 Apr" for daily strategies, "7 Apr 09:45" for intraday ones. */
function when(
  trade: LabTrade,
  side: "opened" | "closed",
  intraday: boolean,
): string {
  const at = side === "opened" ? trade.opened_at : trade.closed_at;
  const day = shortDate(trade[side]);
  if (!intraday || !at) return day;
  const time = new Date(at).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Kolkata",
  });
  return `${day} ${time}`;
}

function daysBetween(a: string, b: string): number {
  return Math.round(
    (new Date(b).getTime() - new Date(a).getTime()) / 86_400_000,
  );
}

interface Group {
  key: string;
  trades: number;
  wins: number;
  pnl: number;
}

function groupBy(
  trades: LabTrade[],
  keyOf: (trade: LabTrade) => string,
): Group[] {
  const groups = new Map<string, Group>();
  for (const trade of trades) {
    const key = keyOf(trade);
    const group = groups.get(key) ?? { key, trades: 0, wins: 0, pnl: 0 };
    group.trades += 1;
    group.wins += trade.net_pnl > 0 ? 1 : 0;
    group.pnl += trade.net_pnl;
    groups.set(key, group);
  }
  return [...groups.values()].sort((a, b) => b.pnl - a.pnl);
}

function GroupTable({
  title,
  groups,
  label,
}: {
  title: string;
  groups: Group[];
  label: (key: string) => string;
}) {
  return (
    <Table>
      <thead>
        <tr>
          <th>{title}</th>
          <th className="right">Trades</th>
          <th className="right">Won</th>
          <th className="right">Net P&amp;L</th>
        </tr>
      </thead>
      <tbody>
        {groups.map((group) => (
          <tr key={group.key}>
            <td>{label(group.key)}</td>
            <td className="right">{group.trades}</td>
            <td className="right">{fraction(group.wins / group.trades, 0)}</td>
            <td className="right">
              <Pnl value={group.pnl}>{signedInr(group.pnl)}</Pnl>
            </td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

function usePage(id: string) {
  const [state, setState] = useState<{
    data: LabBacktestPage | null;
    error: string | null;
  }>({
    data: null,
    error: null,
  });
  const [version, setVersion] = useState(0);
  const shown = useRef(id);
  useEffect(() => {
    let active = true;
    // A new strategy starts blank; a reload of the same one keeps the page in place.
    if (shown.current !== id || version === 0)
      setState({ data: null, error: null });
    shown.current = id;
    api
      .labBacktestPage(id)
      .then((data) => active && setState({ data, error: null }))
      .catch(
        (err: Error) => active && setState({ data: null, error: err.message }),
      );
    return () => {
      active = false;
    };
  }, [id, version]);
  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { ...state, reload };
}

export function LabBacktestView({ id }: { id: string }) {
  const { data, error, reload } = usePage(id);
  const [windowKey, setWindowKey] = useState<WindowKey>("test");
  const [period, setPeriod] = useState<Period>("week");
  const [periodView, setPeriodView] = useState<"heat" | "bars">("heat");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<number>(PAGE_SIZES[0]);
  const [symbol, setSymbol] = useState("");
  const [highlight, setHighlight] = useState<number | null>(null);

  const result = data?.result ?? null;
  const detail = result?.detail;
  const parts = useMemo(() => {
    if (!detail) return [];
    return windowKey === "both"
      ? [detail.train, detail.test]
      : [detail[windowKey]];
  }, [detail, windowKey]);
  const trades = useMemo(() => parts.flatMap((part) => part.trades), [parts]);
  const daily = useMemo(() => {
    const merged: Record<string, number> = {};
    for (const part of parts)
      for (const [day, pnl] of Object.entries(part.daily))
        merged[day] = (merged[day] ?? 0) + pnl;
    return Object.entries(merged).sort(([a], [b]) => a.localeCompare(b));
  }, [parts]);

  const equity = useMemo(() => {
    let total = 0;
    return [
      {
        name: "Cumulative P&L",
        color: "var(--series-1)",
        points: daily.map(([day, pnl]) => {
          total += pnl;
          return { x: new Date(`${day}T15:30:00+05:30`).getTime(), y: total };
        }),
      },
    ];
  }, [daily]);

  const periods = useMemo(() => {
    const totals = new Map<string, number>();
    for (const [day, pnl] of daily) {
      const key = periodKey(day, period);
      totals.set(key, (totals.get(key) ?? 0) + pnl);
    }
    const tradeCounts = new Map<string, number>();
    for (const trade of trades) {
      const key = periodKey(trade.closed, period);
      tradeCounts.set(key, (tradeCounts.get(key) ?? 0) + 1);
    }
    return [...totals.entries()].map(([key, value]) => ({
      key,
      value,
      trades: tradeCounts.get(key) ?? 0,
      note: `${tradeCounts.get(key) ?? 0} trades closed`,
    }));
  }, [daily, trades, period]);

  // Newest first, keeping each trade's place in the run for highlighting it on the chart.
  const newestFirst = useMemo(
    () => trades.map((trade, i) => ({ trade, i })).reverse(),
    [trades],
  );
  const tradePage = pageOf(newestFirst, page, pageSize);
  // Another window or test brings other trades: start again from the newest.
  useEffect(() => setPage(0), [trades]);

  const symbols = Object.keys(data?.bars ?? {});
  const shownSymbol = symbols.includes(symbol) ? symbol : symbols[0];
  const bars = useMemo(() => {
    const all = (shownSymbol && data?.bars[shownSymbol]) || [];
    if (!result || windowKey === "both") return all;
    const w = result.windows;
    const [from, to] =
      windowKey === "test"
        ? [w.test_start, w.test_end]
        : [w.train_start, w.train_end];
    return all.filter((bar) => bar.day >= from && bar.day <= to);
  }, [data, shownSymbol, result, windowKey]);
  const closeOn = useMemo(
    () => new Map(bars.map((bar) => [bar.day, bar.close])),
    [bars],
  );
  const intradayTimes = (data?.spec.decide_every ?? "day") !== "day";
  const chartTrades: ChartTrade[] = useMemo(
    () =>
      trades
        .map((trade, i) => ({ trade, i }))
        .filter(({ trade }) => trade.symbol === shownSymbol)
        .map(({ trade, i }) => ({
          id: i,
          opened: trade.opened,
          closed: trade.closed,
          pnl: trade.net_pnl,
          title: `${when(trade, "opened", intradayTimes)} → ${when(trade, "closed", intradayTimes)}: ${signedInr(trade.net_pnl)}`,
          lines: [
            legText(trade),
            `${EXIT_REASONS[trade.exit_reason ?? ""] ?? trade.exit_reason ?? "–"}, ${daysBetween(trade.opened, trade.closed)} days`,
            `Index ${num(closeOn.get(trade.opened), 0)} → ${num(closeOn.get(trade.closed), 0)}`,
            `Trend ${trade.trend}, IV ${trade.iv_regime}`,
          ],
        })),
    [trades, shownSymbol, closeOn, intradayTimes],
  );

  if (error) return <Empty title="Could not load this backtest">{error}</Empty>;
  if (!data) return <Empty title="Loading…" />;
  const { spec } = data;
  const intraday = (spec.decide_every ?? "day") !== "day";
  const back = `#lab/view/${encodeURIComponent(spec.id ?? id)}`;
  const verdict = result ? VERDICTS[result.verdict] : null;

  const wins = trades.filter((t) => t.net_pnl > 0);
  const losses = trades.filter((t) => t.net_pnl <= 0);
  const net = trades.reduce((sum, t) => sum + t.net_pnl, 0);
  const grossWin = wins.reduce((sum, t) => sum + t.net_pnl, 0);
  const grossLoss = -losses.reduce((sum, t) => sum + t.net_pnl, 0);
  const best = trades.reduce<LabTrade | null>(
    (b, t) => (b === null || t.net_pnl > b.net_pnl ? t : b),
    null,
  );
  const worst = trades.reduce<LabTrade | null>(
    (b, t) => (b === null || t.net_pnl < b.net_pnl ? t : b),
    null,
  );
  let peak = 0;
  let drawdown = 0;
  for (const point of equity[0].points) {
    peak = Math.max(peak, point.y ?? 0);
    drawdown = Math.max(drawdown, peak - (point.y ?? 0));
  }
  const greenPeriods = periods.filter((p) => p.value > 0).length;
  const activePeriods = periods.filter((p) => p.value !== 0).length;
  const windowText = result
    ? windowKey === "train"
      ? `${shortDate(result.windows.train_start)} to ${shortDate(result.windows.train_end)}`
      : windowKey === "test"
        ? `${shortDate(result.windows.test_start)} to ${shortDate(result.windows.test_end)}`
        : `${shortDate(result.windows.train_start)} to ${shortDate(result.windows.test_end)}`
    : "";
  const spans =
    result && windowKey === "both"
      ? [
          {
            from: result.windows.train_start,
            to: result.windows.train_end,
            label: "Training",
          },
          {
            from: result.windows.test_start,
            to: result.windows.test_end,
            label: "Test",
          },
        ]
      : [];

  return (
    <>
      <div className="strike-page-head">
        <BackLink href={back}>Strategy lab</BackLink>
        <div>
          <h2 className="strike-page-title">
            {spec.name} <StageBadge stage={data.stage} />
          </h2>
          <div className="legs">
            {data.legs_text.split(", ").map((leg) => (
              <code key={leg}>{leg}</code>
            ))}
          </div>
          <span className="muted">Enter {data.entry_text}</span>
        </div>
        <ButtonLink
          icon={Pencil}
          href={`#lab/${spec.source === "template" ? "copy" : "edit"}/${encodeURIComponent(spec.id as string)}`}
        >
          {spec.source === "template" ? "Copy and edit" : "Edit"}
        </ButtonLink>
      </div>

      {!result ? (
        <Empty title="Not tested yet">
          Test this strategy in the Strategy lab first.
        </Empty>
      ) : !detail ? (
        <Empty title="This result has no trade detail">
          It was tested before trade detail was kept. Test it again in the
          Strategy lab.
        </Empty>
      ) : (
        <>
          <div className="filters">
            <LabeledSelect
              label="Window"
              value={windowKey}
              onChange={setWindowKey}
              options={Object.entries(WINDOWS).map(([value, label]) => ({
                value: value as WindowKey,
                label,
              }))}
            />
            {symbols.length > 1 && (
              <LabeledSelect
                label="Underlying"
                value={shownSymbol ?? ""}
                onChange={setSymbol}
                options={symbols.map((s) => ({ value: s, label: s }))}
              />
            )}
            <span className="muted">
              {windowText}, after costs. Tested{" "}
              {dateTimeIst(result.evaluated_at)}.
            </span>
          </div>

          <Tiles columns={4}>
            <Tile
              label="Net P&L"
              value={<Pnl value={net}>{signedInr(net)}</Pnl>}
              hint={`${fraction(net / data.capital, 1)} of ₹${num(data.capital)} capital`}
            />
            <Tile
              label="Trades"
              value={num(trades.length)}
              hint={`${wins.length} won, ${losses.length} lost`}
            />
            <Tile
              label="Win rate"
              value={
                trades.length ? fraction(wins.length / trades.length, 0) : "–"
              }
              hint={`Profit factor ${grossLoss > 0 ? num(grossWin / grossLoss, 2) : "–"}`}
            />
            <Tile
              label="Max drawdown"
              value={signedInr(-drawdown)}
              hint="From the highest point"
            />
            <Tile
              label="Average win"
              value={wins.length ? signedInr(grossWin / wins.length) : "–"}
              hint={
                best
                  ? `Best ${signedInr(best.net_pnl)} on ${shortDate(best.opened)}`
                  : undefined
              }
            />
            <Tile
              label="Average loss"
              value={
                losses.length ? signedInr(-grossLoss / losses.length) : "–"
              }
              hint={
                worst
                  ? `Worst ${signedInr(worst.net_pnl)} on ${shortDate(worst.opened)}`
                  : undefined
              }
            />
            <Tile
              label={`Profitable ${PERIOD_PLURAL[period]}`}
              value={
                activePeriods ? `${greenPeriods} of ${activePeriods}` : "–"
              }
              hint="Periods with any P&L"
            />
            <Tile
              label="Average holding"
              value={
                trades.length
                  ? `${num(trades.reduce((s, t) => s + daysBetween(t.opened, t.closed), 0) / trades.length, 1)} days`
                  : "–"
              }
            />
          </Tiles>

          <Panel
            title="Research stage"
            aside={
              verdict && (
                <span className="muted">Lab verdict: {verdict.label}</span>
              )
            }
          >
            <StageCard stage={data.stage} stages={data.stages} />
          </Panel>

          {data.regimes && (
            <Panel
              title="Market regimes"
              aside={
                <span className="muted">Training and test trades together</span>
              }
            >
              <RegimeTable regimes={data.regimes} />
            </Panel>
          )}

          {windowKey !== "both" && result[windowKey] && (
            <Panel
              title="All statistics"
              aside={
                <span className="muted">
                  {WINDOWS[windowKey]}, after costs, on a ₹{num(data.capital)}{" "}
                  budget
                </span>
              }
            >
              <MetricsGrid summary={result[windowKey]} />
            </Panel>
          )}

          <Panel
            title={`${shownSymbol ?? "Underlying"} with every trade`}
            aside={
              <span className="muted">
                Hollow dot: entry. Solid dot: exit. Green won, red lost.
              </span>
            }
          >
            {bars.length < 2 ? (
              <Empty title="No daily bars for this window" />
            ) : (
              <TradeChart
                bars={bars}
                trades={chartTrades}
                highlight={highlight}
                onHighlight={setHighlight}
                spans={spans}
                height={380}
                label={`${shownSymbol} daily close with the entries and exits of ${spec.name}`}
                formatY={(y) => num(y)}
                formatDay={(day) => shortDate(day)}
              />
            )}
          </Panel>

          {data.technical && (
            <Panel
              title="Replay every trade"
              aside={
                <span className="muted">
                  The strategy's lines, its entries and exits, and the open
                  position, candle by candle
                </span>
              }
            >
              <IndexChart
                key={windowKey}
                strategyPreview
                catalog={data.technical}
                symbols={data.chart_symbols ?? data.symbols ?? symbols}
                tradable={data.symbols}
                rules={spec.entry.rules ?? []}
                anyRules={spec.entry.any_rules ?? []}
                draft={spec}
                replaySource={{
                  label: WINDOWS[windowKey].toLowerCase(),
                  load: (chartSymbol) =>
                    api.labBacktestReplay(
                      spec.id as string,
                      windowKey,
                      chartSymbol,
                    ),
                }}
              />
            </Panel>
          )}

          <Panel title="Cumulative P&L">
            {equity[0].points.length < 2 ? (
              <Empty title="Not enough sessions to chart" />
            ) : (
              <LineChart
                series={equity}
                height={220}
                polarity
                includeZero
                tickX={timeTicks}
                label={`Cumulative P&L of ${spec.name}`}
                formatX={(x) =>
                  shortDate(new Date(x + 19800000).toISOString().slice(0, 10))
                }
                formatY={inrCompact}
                formatTipX={(x) =>
                  shortDate(new Date(x + 19800000).toISOString().slice(0, 10))
                }
                formatTipY={(y) => signedInr(y)}
              />
            )}
          </Panel>

          <div className="panel-pair">
            <Panel
              title="Profit and loss by period"
              aside={
                <div className="period-controls">
                  <Segmented
                    label="Show as"
                    value={periodView}
                    onChange={setPeriodView}
                    options={[
                      { value: "heat", label: "Heat map" },
                      { value: "bars", label: "Bars" },
                    ]}
                  />
                  <LabeledSelect
                    label="Period"
                    value={period}
                    onChange={setPeriod}
                    options={Object.entries(PERIODS).map(([value, label]) => ({
                      value: value as Period,
                      label,
                    }))}
                  />
                </div>
              }
            >
              {periods.length === 0 ? (
                <Empty title="No P&L in this window" />
              ) : periodView === "heat" ? (
                <PnlHeatmap daily={daily} period={period} />
              ) : (
                <ValueColumns
                  data={periods}
                  height={220}
                  label={`${PERIODS[period]} P&L of ${spec.name}`}
                  formatX={(key) => periodLabel(key, period)}
                  formatY={inrCompact}
                  formatTip={(value) => signedInr(value)}
                />
              )}
              {periodView === "bars" &&
                period !== "day" &&
                periods.length > 0 && (
                  <Table>
                    <thead>
                      <tr>
                        <th>{period === "week" ? "Week of" : "Month"}</th>
                        <th className="right">Trades closed</th>
                        <th className="right">P&amp;L</th>
                        <th className="right">Running total</th>
                      </tr>
                    </thead>
                    <tbody>
                      {runningRows(periods).map((p) => (
                        <tr key={p.key}>
                          <td>{periodLabel(p.key, period)}</td>
                          <td className="right">{p.trades}</td>
                          <td className="right">
                            <Pnl value={p.value}>{signedInr(p.value)}</Pnl>
                          </td>
                          <td className="right">
                            <Pnl value={p.total}>{signedInr(p.total)}</Pnl>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                )}
            </Panel>
            {trades.length > 0 && (
              <Panel title="By weekday and entry time">
                <TimingGrid cells={timingFromTrades(trades)} />
              </Panel>
            )}
          </div>

          <h3 className="section-title">
            Where it worked and where it did not
          </h3>
          {trades.length === 0 ? (
            <Empty title="No trades in this window">
              The Strategy lab lists why days were skipped.
            </Empty>
          ) : (
            <div className="grid-3">
              <Panel title="By how the trade ended">
                <GroupTable
                  title="Exit"
                  groups={groupBy(trades, (t) => t.exit_reason ?? "–")}
                  label={(k) => EXIT_REASONS[k] ?? k}
                />
              </Panel>
              <Panel title="By trend at entry">
                <GroupTable
                  title="Trend"
                  groups={groupBy(trades, (t) => t.trend)}
                  label={(k) => k}
                />
              </Panel>
              <Panel title="By volatility at entry">
                <GroupTable
                  title="IV rank"
                  groups={groupBy(trades, (t) => t.iv_regime)}
                  label={(k) => k}
                />
              </Panel>
            </div>
          )}

          {trades.length > 0 && (
            <Panel
              title="Every trade"
              aside={
                <span className="muted">
                  Hover a row to find it on the chart
                </span>
              }
            >
              <Table>
                <thead>
                  <tr>
                    <th>Opened</th>
                    <th>Closed</th>
                    <th className="right">Days</th>
                    <th className="right">Index close, in → out</th>
                    <th>Legs, entry → exit price</th>
                    <th className="right">Lots</th>
                    <th>Exit</th>
                    <th className="right">Net P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {tradePage.shown.map(({ trade, i }) => (
                    <tr
                      key={i}
                      className={highlight === i ? "row-active" : ""}
                      onMouseEnter={() =>
                        trade.symbol === shownSymbol && setHighlight(i)
                      }
                      onMouseLeave={() => setHighlight(null)}
                    >
                      <td>{when(trade, "opened", intraday)}</td>
                      <td>{when(trade, "closed", intraday)}</td>
                      <td className="right">
                        {daysBetween(trade.opened, trade.closed)}
                      </td>
                      <td className="right">
                        {num(closeOn.get(trade.opened), 0)} →{" "}
                        {num(closeOn.get(trade.closed), 0)}
                      </td>
                      <td className="secondary">{priceText(trade)}</td>
                      <td className="right">{trade.lots}</td>
                      <td>
                        {EXIT_REASONS[trade.exit_reason ?? ""] ??
                          trade.exit_reason}
                        {trade.settlement_estimated ? " (intrinsic)" : ""}
                      </td>
                      <td className="right">
                        <Pnl value={trade.net_pnl}>
                          {signedInr(trade.net_pnl)}
                        </Pnl>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>

              <Pager
                page={tradePage.page}
                pages={tradePage.pages}
                size={pageSize}
                total={trades.length}
                noun="trade"
                onPage={setPage}
                onSize={(size) => {
                  // Keep the first trade on screen in view at the new size.
                  setPage(Math.floor((tradePage.page * pageSize) / size));
                  setPageSize(size);
                }}
              />
            </Panel>
          )}
          <Panel
            title="Parameter search"
            aside={
              data.search && (
                <span className="muted">
                  This strategy came from a {data.search.method} search of{" "}
                  {data.search.trials} settings
                </span>
              )
            }
          >
            <SearchPanel specId={id} onFinished={reload} />
          </Panel>

          <Panel title="Research dossier">
            <DossierPanel specId={id} />
          </Panel>

          <p className="panel-note">
            Fills use the next session&apos;s open with modelled spreads, after
            brokerage, taxes and slippage. Positions held to expiry settle at
            intrinsic value. The training window is where rules were chosen;
            only the test window is unseen.
          </p>
        </>
      )}
    </>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import {
  api,
  type IndexRow,
  type MoverRow,
  type OverviewComparison,
  type OverviewHistory,
  type OverviewPayload,
} from "../api";
import { Candles, Histogram, Legend, LineChart, Treemap, heatClass, timeTicks } from "../charts";
import { compact, dateTimeIst, fraction, inrCompact, num, shortDate, timeIst } from "../format";
import { Empty, Panel, Select, Status, type Tone } from "../ui";

const POLL_MS = 30000;
const PERIODS = ["1D", "1W", "1M", "3M", "1Y"] as const;
const CHART_POLL_MS = 60000;
const RANGES = { "1M": 21, "3M": 63, "6M": 126, "1Y": 252 } as const;
type Range = keyof typeof RANGES;
const COMPARE_COLORS = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
];
// A 2% day and a 30% year are both strong moves, so the colour scale widens with the period.
const HEAT_SCALE: Record<string, number> = { "1D": 1, "1W": 2, "1M": 4, "3M": 7.5, "1Y": 15 };

function dateMs(iso: string): number {
  return new Date(`${iso.slice(0, 10)}T15:30:00+05:30`).getTime();
}

function dayLabel(x: number): string {
  return shortDate(new Date(x + 19800000).toISOString().slice(0, 10));
}
type Period = (typeof PERIODS)[number];

function signedPct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "–";
  const rounded = Number(value.toFixed(digits));
  return `${rounded > 0 ? "+" : ""}${(rounded === 0 ? 0 : rounded).toFixed(digits)}%`;
}

function Change({ value }: { value: number | null | undefined }) {
  const direction = !value ? "" : value > 0 ? "pnl-up" : "pnl-down";
  return <span className={direction}>{signedPct(value)}</span>;
}

function periodValue(row: IndexRow, period: Period): number | null {
  return period === "1D" ? row.change_pct : (row.returns[period] ?? null);
}

function statusFor(status: OverviewPayload["status"]): { tone: Tone; label: string } {
  if (status === "live") return { tone: "good", label: "Live" };
  if (status === "closed") return { tone: "neutral", label: "Market closed, last session" };
  if (status === "session_expired") return { tone: "warning", label: "Login needed, showing last data" };
  return { tone: "warning", label: "Upstox unavailable, showing last data" };
}

function IndexTile({ row }: { row: IndexRow }) {
  return (
    <div className="tile index-tile">
      <div className="tile-label">{row.label}</div>
      <div className="tile-value">{num(row.last, 2)}</div>
      <div className="index-change">
        <Change value={row.change_pct} />
        <span className="muted">{num(row.change, 2)}</span>
      </div>
      {row.range_position !== null && (
        <div className="day-range" title={`Day range ${num(row.low, 2)} to ${num(row.high, 2)}`}>
          <span style={{ left: `${Math.max(0, Math.min(100, row.range_position))}%` }} />
        </div>
      )}
    </div>
  );
}

function SectorBars({ sectors, period }: { sectors: IndexRow[]; period: Period }) {
  const rows = sectors
    .map((row) => ({ row, value: periodValue(row, period) }))
    .filter((item): item is { row: IndexRow; value: number } => item.value !== null)
    .sort((a, b) => b.value - a.value);
  if (!rows.length) return <Empty title="This period's history is still loading" />;
  const scale = Math.max(...rows.map((item) => Math.abs(item.value)), 0.5);
  return (
    <div className="sector-bars" role="img" aria-label={`Sector returns over ${period}, sorted`}>
      {rows.map(({ row, value }) => {
        const width = (Math.abs(value) / scale) * 50;
        return (
          <div className="sector-bar" key={row.name} title={`${row.label}: ${signedPct(value)}`}>
            <span className="sector-name">{row.label}</span>
            <span className="sector-track">
              <i
                className={value >= 0 ? "bar-up" : "bar-down"}
                style={value >= 0 ? { left: "50%", width: `${width}%` } : { right: "50%", width: `${width}%` }}
              />
            </span>
            <span className="sector-value">{signedPct(value)}</span>
          </div>
        );
      })}
    </div>
  );
}

function BreadthPanel({ data }: { data: OverviewPayload }) {
  const breadth = data.breadth;
  return (
    <Panel title="Market breadth" ai={breadth ? { component: "breadth", title: "Market breadth" } : undefined} aside={breadth ? <span className="muted">{breadth.stocks} stocks traded</span> : undefined}>
      {!breadth ? (
        <Empty title="Needs the industry list">Upload it below to see breadth.</Empty>
      ) : (
        <div className="breadth">
          <div className="breadth-numbers">
            <div>
              <b>{breadth.advancing}</b>
              <span>Advancing</span>
            </div>
            <div>
              <b>{breadth.declining}</b>
              <span>Declining</span>
            </div>
            <div>
              <b>{breadth.unchanged}</b>
              <span>Unchanged</span>
            </div>
          </div>
          <div className="segments" role="img" aria-label={`${breadth.advancing} advancing, ${breadth.declining} declining`}>
            <i className="bar-up" style={{ flexGrow: breadth.advancing }} />
            <i className="bar-down" style={{ flexGrow: breadth.declining }} />
          </div>
          <dl className="facts">
            <div>
              <dt>Average stock today</dt>
              <dd>
                <Change value={breadth.average_change} />
              </dd>
            </div>
            <div>
              <dt>Value traded</dt>
              <dd>{inrCompact(breadth.value)}</dd>
            </div>
            <div>
              <dt>Up 2% or more</dt>
              <dd>{breadth.up_over_2}</dd>
            </div>
            <div>
              <dt>Down 2% or more</dt>
              <dd>{breadth.down_over_2}</dd>
            </div>
          </dl>
        </div>
      )}
    </Panel>
  );
}

function OptionsPanel({ data }: { data: OverviewPayload }) {
  return (
    <Panel title="Options positioning" ai={data.options.length ? { component: "options", title: "Options positioning" } : undefined} aside={<a className="muted" href="#market">Open chains</a>}>
      {data.options.length === 0 ? (
        <Empty title="No option snapshot yet">Press Refresh, or leave the collector running.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Index</th>
                <th className="right">ATM IV</th>
                <th className="right">IV rank</th>
                <th className="right">Put-call</th>
                <th className="right">Max pain</th>
                <th className="right">Move ±</th>
              </tr>
            </thead>
            <tbody>
              {data.options.map((row) => (
                <tr key={row.symbol}>
                  <td>
                    <div className="primary">{row.symbol}</div>
                    <div className="secondary">{row.expiry ? shortDate(row.expiry) : "–"}</div>
                  </td>
                  <td className="right">{fraction(row.atm_iv)}</td>
                  <td className="right">{num(row.iv_rank, 0)}</td>
                  <td className="right">{num(row.pcr, 2)}</td>
                  <td className="right">{num(row.max_pain)}</td>
                  <td className="right">{num(row.expected_move)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function HeatCell({ value, period }: { value: number | null; period: string }) {
  if (value === null) return <td className="right heat-cell">–</td>;
  return (
    <td className="right heat-cell">
      <span className={`heat-${heatClass(value / HEAT_SCALE[period])}`}>{signedPct(value)}</span>
    </td>
  );
}

function PriceChart({ names, range }: { names: { value: string; label: string }[]; range: Range }) {
  const [name, setName] = useState("Nifty 50");
  const [history, setHistory] = useState<OverviewHistory | null>(null);
  useEffect(() => {
    let active = true;
    const load = () =>
      api
        .overviewHistory(name)
        .then((body) => active && setHistory(body))
        .catch(() => undefined);
    load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, CHART_POLL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [name]);
  const candles = history?.name === name ? history.candles.slice(-RANGES[range]) : [];
  return (
    <Panel
      title="Price chart"
      ai={{ component: "price_chart", title: `${history?.label ?? "Index"} price chart, ${range}`, params: { name, range } }}
      aside={
        <span className="chart-controls">
          <Legend
            items={[
              { name: "Up", color: "var(--series-1)" },
              { name: "Down", color: "var(--bar-down)" },
              { name: "EMA 20", color: "var(--overlay-1)" },
              { name: "EMA 50", color: "var(--overlay-2)" },
            ]}
          />
          <Select label="Index" value={name} onChange={setName} options={names} />
        </span>
      }
    >
      {candles.length < 2 ? (
        <Empty title="Price history is still loading" />
      ) : (
        <Candles
          data={candles}
          label={`Daily candles with 20 and 50 session moving averages for ${history?.label}`}
          formatDate={shortDate}
          formatPrice={(value) => num(value)}
        />
      )}
    </Panel>
  );
}

function CompareChart({ data, range }: { data: OverviewComparison | null; range: Range }) {
  const count = RANGES[range];
  const series = useMemo(() => {
    if (!data) return [];
    const dates = data.dates.slice(-count);
    return data.series.map((item, index) => {
      const closes = item.closes.slice(-count);
      return {
        name: item.label,
        color: COMPARE_COLORS[index % COMPARE_COLORS.length],
        points: closes.map((close, i) => ({
          x: dateMs(dates[i]),
          y: (close / closes[0] - 1) * 100,
        })),
      };
    });
  }, [data, count]);
  return (
    <Panel
      title={`Index comparison, ${range} return`}
      ai={{ component: "comparison", title: `Index comparison over ${range}`, params: { range } }}
      aside={<Legend items={series.map(({ name, color }) => ({ name, color }))} />}
    >
      {series.length === 0 || series[0].points.length < 2 ? (
        <Empty title="Index history is still loading" />
      ) : (
        <LineChart
          series={series}
          height={280}
          includeZero
          tickX={timeTicks}
          label={`Index returns over ${range}, all starting from zero`}
          formatX={dayLabel}
          formatY={(y) => `${y.toFixed(0)}%`}
          formatTipY={(y) => signedPct(y)}
        />
      )}
    </Panel>
  );
}

function VixChart({ data, range }: { data: OverviewComparison | null; range: Range }) {
  const count = RANGES[range];
  const series = useMemo(() => {
    if (!data) return [];
    const dates = data.volatility.dates.slice(-count);
    const closes = data.volatility.closes.slice(-count);
    return [
      {
        name: "India VIX",
        color: "var(--series-1)",
        points: closes.map((close, i) => ({ x: dateMs(dates[i]), y: close })),
      },
    ];
  }, [data, count]);
  return (
    <Panel title="India VIX" ai={{ component: "vix", title: `India VIX over ${range}`, params: { range } }}>
      {series.length === 0 || series[0].points.length < 2 ? (
        <Empty title="VIX history is still loading" />
      ) : (
        <LineChart
          series={series}
          height={280}
          minSpan={2}
          tickX={timeTicks}
          label={`India VIX over ${range}`}
          formatX={dayLabel}
          formatY={(y) => y.toFixed(0)}
          formatTipY={(y) => y.toFixed(2)}
        />
      )}
    </Panel>
  );
}

function IndustryBars({ data }: { data: OverviewPayload }) {
  const rows = data.industries.slice(0, 24);
  const scale = Math.max(...rows.map((row) => Math.abs(row.average_change)), 0.5);
  return (
    <div className="sector-bars" role="img" aria-label="Average change today by industry">
      {rows.map((row) => {
        const value = row.average_change;
        const width = (Math.abs(value) / scale) * 50;
        return (
          <div className="sector-bar" key={row.industry} title={`${row.industry}: ${signedPct(value)}`}>
            <span className="sector-name">{row.industry}</span>
            <span className="sector-track">
              <i
                className={value >= 0 ? "bar-up" : "bar-down"}
                style={value >= 0 ? { left: "50%", width: `${width}%` } : { right: "50%", width: `${width}%` }}
              />
            </span>
            <span className="sector-value">{signedPct(value)}</span>
          </div>
        );
      })}
    </div>
  );
}

function SectorTable({ sectors }: { sectors: IndexRow[] }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Sector</th>
            <th className="right">Last</th>
            <th className="right">Today</th>
            <th className="right">vs Nifty</th>
            <th className="right">1W</th>
            <th className="right">1M</th>
            <th className="right">3M</th>
            <th className="right">1Y</th>
            <th>Day range</th>
          </tr>
        </thead>
        <tbody>
          {sectors.map((row) => (
            <tr key={row.name}>
              <td className="primary">{row.label}</td>
              <td className="right">{num(row.last, 2)}</td>
              <HeatCell value={row.change_pct} period="1D" />
              <td className="right">
                <Change value={row.vs_nifty} />
              </td>
              {(["1W", "1M", "3M", "1Y"] as const).map((period) => (
                <HeatCell value={row.returns[period] ?? null} period={period} key={period} />
              ))}
              <td>
                {row.range_position !== null && (
                  <div className="day-range table-range" title={`${num(row.low, 2)} to ${num(row.high, 2)}`}>
                    <span style={{ left: `${Math.max(0, Math.min(100, row.range_position))}%` }} />
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function IndustryTable({ data }: { data: OverviewPayload }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Industry</th>
            <th className="right">Stocks</th>
            <th className="right">Average</th>
            <th>Advancing / declining</th>
            <th className="right">Value traded</th>
            <th>Best</th>
            <th>Worst</th>
          </tr>
        </thead>
        <tbody>
          {data.industries.map((row) => (
            <tr key={row.industry}>
              <td className="primary">{row.industry}</td>
              <td className="right">{row.stocks}</td>
              <td className="right">
                <Change value={row.average_change} />
              </td>
              <td>
                <div className="mini-breadth">
                  <span className="segments thin">
                    <i className="bar-up" style={{ flexGrow: row.advancing }} />
                    <i className="bar-down" style={{ flexGrow: row.declining }} />
                  </span>
                  <small>
                    {row.advancing} / {row.declining}
                  </small>
                </div>
              </td>
              <td className="right">{inrCompact(row.value)}</td>
              <td className="secondary-nowrap">
                {row.best.symbol} <Change value={row.best.change_pct} />
              </td>
              <td className="secondary-nowrap">
                {row.worst.symbol} <Change value={row.worst.change_pct} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MoverTable({ title, rows, list, showValue }: { title: string; rows: MoverRow[]; list: string; showValue?: boolean }) {
  return (
    <Panel title={title} ai={{ component: "movers", title, params: { list } }}>
      <div className="table-wrap">
        <table className="table compact-table">
          <thead>
            <tr>
              <th>Stock</th>
              <th className="right">Last</th>
              <th className="right">{showValue ? "Value" : "Change"}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.symbol} title={`${row.name}, ${row.industry}`}>
                <td>
                  <div className="primary">{row.symbol}</div>
                  <div className="secondary truncate">{row.industry}</div>
                </td>
                <td className="right">{num(row.last, 2)}</td>
                <td className="right">
                  {showValue ? (
                    <>
                      <div>{inrCompact(row.value)}</div>
                      <small>
                        <Change value={row.change_pct} />
                      </small>
                    </>
                  ) : (
                    <Change value={row.change_pct} />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function SectorUpload({ onDone, replace }: { onDone: () => void; replace?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const upload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.uploadSectors(file);
      setMessage(`Loaded ${compact(result.stocks)} stocks.`);
      onDone();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="upload">
      <input ref={input} type="file" accept=".csv,text/csv" onChange={upload} hidden />
      <button
        className={replace ? "button ghost" : "button"}
        disabled={busy}
        onClick={() => input.current?.click()}
      >
        {busy ? "Uploading…" : replace ? "Replace list" : "Upload industry list"}
      </button>
      {message && <span className="muted">{message}</span>}
    </span>
  );
}

function HeatLegend() {
  const labels = ["≤ −2%", "−1%", "−0.25%", "0", "+0.25%", "+1%", "≥ +2%"];
  return (
    <div className="heat-legend" aria-hidden>
      {labels.map((label, index) => (
        <span key={label} className={`heat-${index}`}>
          {label}
        </span>
      ))}
    </div>
  );
}

export function OverviewView() {
  const [data, setData] = useState<OverviewPayload | null>(null);
  const [failed, setFailed] = useState(false);
  const [period, setPeriod] = useState<Period>("1D");
  const [range, setRange] = useState<Range>("3M");
  const [comparison, setComparison] = useState<OverviewComparison | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.overview());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    let active = true;
    const load = () =>
      api
        .overviewComparison()
        .then((body) => active && setComparison(body))
        .catch(() => undefined);
    load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, CHART_POLL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  if (!data) {
    return failed ? <Empty title="Could not load the market overview" /> : <Empty title="Loading the market…" />;
  }
  if (!data.indices.length) {
    return <Empty title="No market data yet">Log in to Upstox to load the market overview.</Empty>;
  }
  const status = statusFor(data.status);
  const tiles = data.volatility ? [...data.indices, data.volatility] : data.indices;
  const sectorsUp = data.sectors.filter((row) => (row.change_pct ?? 0) > 0).length;
  return (
    <>
      <div className="filters overview-head">
        <Status tone={status.tone}>{status.label}</Status>
        <span className="muted">
          {data.last_trade_at ? `Last trade ${dateTimeIst(data.last_trade_at)}` : ""}
          {data.fetched_at ? `, fetched ${timeIst(data.fetched_at)}` : ""}
        </span>
      </div>
      <Panel title="Indices" ai={{ component: "indices", title: "Main indices and India VIX" }}>
        <div className="tiles index-strip">
          {tiles.map((row) => (
            <IndexTile row={row} key={row.name} />
          ))}
        </div>
      </Panel>

      <div className="filters">
        <Select
          label="Chart range"
          value={range}
          onChange={setRange}
          options={(Object.keys(RANGES) as Range[]).map((value) => ({ value, label: value }))}
        />
      </div>
      <div className="overview-grid">
        <PriceChart
          range={range}
          names={[...data.indices, ...data.sectors.slice().sort((a, b) => a.label.localeCompare(b.label))]
            .filter((row, index, all) => all.findIndex((item) => item.name === row.name) === index)
            .map((row) => ({ value: row.name, label: row.label }))}
        />
        <VixChart data={comparison} range={range} />
      </div>
      <div className="overview-grid">
        <CompareChart data={comparison} range={range} />
        <BreadthPanel data={data} />
      </div>
      <div className="overview-grid">
        <Panel
          title={`Sector performance, ${sectorsUp} of ${data.sectors.length} up today`}
          ai={{ component: "sector_performance", title: `Sector performance over ${period}`, params: { period } }}
          aside={
            <Select
              label="Period"
              value={period}
              onChange={setPeriod}
              options={PERIODS.map((value) => ({ value, label: value }))}
            />
          }
        >
          <SectorBars sectors={data.sectors} period={period} />
        </Panel>
        <OptionsPanel data={data} />
      </div>

      <Panel
        title="Market map"
        ai={data.heatmap.length ? { component: "market_map", title: "Market map" } : undefined}
        aside={
          data.has_sector_list ? (
            <span className="map-aside">
              <HeatLegend />
              <SectorUpload onDone={load} replace />
            </span>
          ) : undefined
        }
      >
        {data.has_sector_list ? (
          data.heatmap.length ? (
            <>
              <Treemap groups={data.heatmap} height={560} label="Most-traded stocks grouped by industry, sized by value traded, coloured by today's change" />
              <p className="panel-note">Most-traded stocks by industry. Size is value traded, colour is today's change.</p>
            </>
          ) : (
            <Empty title="No traded stocks in this snapshot" />
          )
        ) : (
          <div className="sector-empty">
            <strong>Add the Nifty 500 industry list to see every stock</strong>
            <p>
              Upstox doesn't say which industry a stock belongs to. Download the Nifty 500 constituents
              CSV from niftyindices.com and upload it once. Breadth, the market map, industries and top
              movers then appear here.
            </p>
            <SectorUpload onDone={load} />
          </div>
        )}
      </Panel>

      {data.distribution.length > 0 && (
        <div className="grid-2">
          <Panel title="How stocks moved today" ai={{ component: "distribution", title: "How stocks moved today" }} aside={<span className="muted">Number of stocks by change</span>}>
            <Histogram
              label="Number of stocks in each band of today's change"
              bars={data.distribution.map((bar, index) => ({
                ...bar,
                tone: index < 5 ? "down" : "up",
              }))}
            />
          </Panel>
          <Panel title="Industry performance today" ai={{ component: "industries", title: "Industry performance today" }} aside={<span className="muted">Equal-weight average</span>}>
            <IndustryBars data={data} />
          </Panel>
        </div>
      )}

      <Panel title="Sectors" ai={{ component: "sector_table", title: "Sector table" }} aside={<span className="muted">Colour scale widens with the period</span>}>
        <SectorTable sectors={data.sectors} />
      </Panel>

      {data.industries.length > 0 && (
        <>
          <Panel title="Industries" ai={{ component: "industries", title: "Industries table" }}>
            <IndustryTable data={data} />
          </Panel>
          <div className="grid-3">
            <MoverTable title="Top gainers" rows={data.movers.gainers} list="gainers" />
            <MoverTable title="Top losers" rows={data.movers.losers} list="losers" />
            <MoverTable title="Most active" rows={data.movers.active} list="active" showValue />
          </div>
        </>
      )}
    </>
  );
}


import { useMemo, useState } from "react";
import type {
  ChainRow,
  ExpiryMarket,
  MarketPayload,
  QuoteSide,
  ResearchBrief,
  SymbolMarket,
} from "../api";
import { Legend, LineChart, PairedColumns } from "../charts";
import { compact, dateTimeIst, fraction, num, pct, shortDate } from "../format";
import { Empty, Panel, Select, Status, Tile, type Tone } from "../ui";

const CALLS = "var(--series-1)";
const PUTS = "var(--series-2)";
const OI_CHART_STRIKES = 31;
const FLAT_TERM_SLOPE = 0.0025;

function otmIv(rows: ChainRow[], forward: number) {
  return rows.map((row) => {
    const side = row.strike >= forward ? row.ce : row.pe;
    return { x: row.strike, y: side?.iv == null ? null : side.iv * 100 };
  });
}

function trendStatus(trend: string | null): { tone: Tone; label: string } {
  if (trend === "up") return { tone: "neutral", label: "Uptrend" };
  if (trend === "down") return { tone: "neutral", label: "Downtrend" };
  if (trend === "range") return { tone: "neutral", label: "Range-bound" };
  return { tone: "neutral", label: "Not enough history" };
}

function Side({ side, cls }: { side: QuoteSide | null; cls: string }) {
  const cells = side
    ? [
        compact(side.oi),
        compact(side.oi_change),
        compact(side.volume),
        side.iv == null ? "–" : (side.iv * 100).toFixed(1),
        num(side.delta, 2),
        num(side.bid, 2),
        num(side.ask, 2),
      ]
    : Array(7).fill("–");
  const ordered = cls === "call" ? cells : [...cells].reverse();
  return (
    <>
      {ordered.map((cell, index) => (
        <td key={index} className={`right ${cls}`}>
          {cell}
        </td>
      ))}
    </>
  );
}

function ChainTable({ expiry }: { expiry: ExpiryMarket }) {
  const nearest = expiry.rows.reduce(
    (best, row) =>
      Math.abs(row.strike - expiry.forward) < Math.abs(best - expiry.forward) ? row.strike : best,
    expiry.rows[0]?.strike ?? 0,
  );
  const callHeads = ["OI", "OI chg", "Vol", "IV", "Delta", "Bid", "Ask"];
  return (
    <div className="table-wrap chain-wrap">
      <table className="table table-chain">
        <thead>
          <tr className="band">
            <th colSpan={7}>Calls</th>
            <th />
            <th colSpan={7}>Puts</th>
          </tr>
          <tr>
            {callHeads.map((head) => (
              <th key={`c-${head}`} className="right">
                {head}
              </th>
            ))}
            <th className="strike">Strike</th>
            {[...callHeads].reverse().map((head) => (
              <th key={`p-${head}`} className="right">
                {head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {expiry.rows.map((row) => (
            <tr key={row.strike} className={row.strike === nearest ? "atm" : ""}>
              <Side side={row.ce} cls="call" />
              <td className="strike">{num(row.strike)}</td>
              <Side side={row.pe} cls="put" />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Brief({ brief }: { brief: ResearchBrief }) {
  return (
    <Panel
      title="News brief"
      aside={
        <span className="muted">
          {brief.model}, {dateTimeIst(brief.generated_at)}
        </span>
      }
    >
      <div className="brief">
        <p>
          <Status tone="neutral">
            {brief.sentiment[0].toUpperCase() + brief.sentiment.slice(1)}
          </Status>{" "}
          {brief.summary}
        </p>
        <div className="brief-columns">
          <div>
            <h4>Catalysts</h4>
            <ul>{brief.catalysts.map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
          <div>
            <h4>Risks</h4>
            <ul>{brief.risks.map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
          <div>
            <h4>Events mentioned</h4>
            {brief.events.length ? (
              <ul>
                {brief.events.map((event) => (
                  <li key={event.name}>
                    {event.name}
                    {event.date ? `, ${event.date}` : ""}
                    {event.in_calendar && <small className="muted"> · added to calendar</small>}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="secondary">None</p>
            )}
          </div>
        </div>
        <p className="secondary">
          AI summary of public headlines. Check it before relying on it. Events with a stated date
          are added to the event calendar automatically. Undated events are not, so keep
          config/events.yaml up to date yourself.
        </p>
        {brief.sources.length > 0 && (
          <details>
            <summary>{brief.sources.length} sources</summary>
            <ul>
              {brief.sources.map((source) => (
                <li key={source.link}>
                  <a href={source.link} target="_blank" rel="noreferrer noopener">
                    {source.title}
                  </a>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </Panel>
  );
}

function SymbolDetail({ market, expiryKey }: { market: SymbolMarket; expiryKey: string }) {
  const expiry = market.expiries.find((e) => e.expiry === expiryKey) ?? market.expiries[0];
  const smile = useMemo(
    () => [
      {
        name: "Out-of-the-money IV",
        color: CALLS,
        points: expiry ? otmIv(expiry.rows, expiry.forward) : [],
      },
    ],
    [expiry],
  );
  const term = useMemo(
    () => [
      {
        name: "ATM IV",
        color: CALLS,
        points: market.term_structure.map((t) => ({ x: t.dte, y: t.atm_iv * 100 })),
      },
    ],
    [market],
  );
  if (!expiry) return <Empty title="No live expiries in this snapshot" />;
  const oiData = [...expiry.rows]
    .sort((a, b) => Math.abs(a.strike - expiry.forward) - Math.abs(b.strike - expiry.forward))
    .slice(0, OI_CHART_STRIKES)
    .sort((a, b) => a.strike - b.strike)
    .map((row) => ({ x: row.strike, a: row.ce?.oi ?? 0, b: row.pe?.oi ?? 0 }));
  const flatTerm = market.term_slope !== null && Math.abs(market.term_slope) < FLAT_TERM_SLOPE;
  const trend = trendStatus(market.trend);
  const proxy = market.iv_source === "india_vix_proxy";
  return (
    <>
      <div className="tiles tiles-6">
        <Tile label="Spot" value={num(market.spot, 2)} hint={<Status tone={trend.tone}>{trend.label}</Status>} />
        <Tile
          label="ATM IV"
          value={fraction(expiry.atm_iv)}
          hint={`Realised 20-day ${fraction(market.realised_vol)}`}
        />
        <Tile
          label="IV rank"
          value={num(market.iv_rank, 0)}
          hint={
            market.iv_sample_size == null
              ? "Not enough history"
              : `${proxy ? "India VIX proxy" : "Own snapshots"}, ${market.iv_sample_size} days`
          }
        />
        <Tile label="Expected move" value={`±${num(expiry.expected_move)}`} hint={`By ${shortDate(expiry.expiry)}`} />
        <Tile label="Put-call ratio" value={num(expiry.pcr, 2)} hint="Total open interest" />
        <Tile
          label="Max pain"
          value={num(expiry.max_pain)}
          hint={`25-delta skew ${expiry.skew == null ? "–" : pct(expiry.skew * 100)}`}
        />
      </div>

      {market.research && <Brief brief={market.research} />}

      <div className="grid-2">
        <Panel title="Open interest by strike" aside={<Legend items={[{ name: "Calls", color: CALLS }, { name: "Puts", color: PUTS }]} />}>
          <PairedColumns
            data={oiData}
            names={["Calls", "Puts"]}
            colors={[CALLS, PUTS]}
            label={`Call and put open interest by strike for ${market.symbol}`}
            formatX={(x) => num(x)}
            formatY={(y) => compact(y)}
            marker={{ x: expiry.forward, label: "Forward" }}
          />
        </Panel>
        <Panel title="Volatility smile">
          <LineChart
            series={smile}
            minSpan={2}
            label={`Implied volatility by strike for ${market.symbol}`}
            formatX={(x) => num(x)}
            formatY={(y) => `${y.toFixed(0)}%`}
            formatTipX={(x) => `Strike ${num(x)}`}
            formatTipY={(y) => `${y.toFixed(1)}%`}
            markers={[{ x: expiry.forward, label: "Forward" }]}
          />
        </Panel>
        <Panel title="Term structure" aside={market.term_slope == null ? undefined : <span className="muted">{flatTerm ? "Flat" : market.term_slope < 0 ? "Backwardation" : "Contango"}</span>}>
          <LineChart
            series={term}
            height={200}
            dots
            minSpan={2}
            label={`At-the-money implied volatility by days to expiry for ${market.symbol}`}
            formatX={(x) => `${x}d`}
            formatY={(y) => `${y.toFixed(1)}%`}
            formatTipX={(x) => `${x} days to expiry`}
            formatTipY={(y) => `${y.toFixed(2)}%`}
          />
        </Panel>
        <Panel title="Strongest levels">
          {expiry.zones.length === 0 ? (
            <Empty title="No levels yet">Levels need daily price history.</Empty>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th className="right">Level</th>
                  <th className="right">From spot</th>
                  <th>Agreeing methods</th>
                </tr>
              </thead>
              <tbody>
                {expiry.zones.map((zone) => (
                  <tr key={zone.center}>
                    <td className="right">{num(zone.center)}</td>
                    <td className="right">{num(zone.center - market.spot)}</td>
                    <td className="secondary">{zone.methods.join(", ").replaceAll("_", " ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>

      <Panel title={`Option chain, ${shortDate(expiry.expiry)}`} aside={<span className="muted">IV in %, within 6% of forward</span>}>
        <ChainTable expiry={expiry} />
      </Panel>
    </>
  );
}

export function MarketView({ market }: { market: MarketPayload }) {
  const symbols = Object.keys(market.symbols);
  const [choice, setChoice] = useState<string>("");
  const [expiry, setExpiry] = useState<string>("");
  const symbol = symbols.includes(choice) ? choice : symbols[0];
  if (!symbol) {
    return (
      <Empty title="No market snapshot yet">
        Market data appears here after the first Refresh or collector cycle.
      </Empty>
    );
  }
  const current = market.symbols[symbol];
  const expiryValue = current.expiries.some((e) => e.expiry === expiry)
    ? expiry
    : (current.expiries[0]?.expiry ?? "");
  return (
    <>
      <div className="filters">
        <Select
          label="Underlying"
          value={symbol}
          onChange={setChoice}
          options={symbols.map((s) => ({ value: s, label: s }))}
        />
        <Select
          label="Expiry"
          value={expiryValue}
          onChange={setExpiry}
          options={current.expiries.map((e) => ({
            value: e.expiry,
            label: `${shortDate(e.expiry)} (${e.dte}d)`,
          }))}
        />
      </div>
      <SymbolDetail market={current} expiryKey={expiryValue} />
    </>
  );
}

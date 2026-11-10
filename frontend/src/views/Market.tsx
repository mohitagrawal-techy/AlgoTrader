import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import {
  api,
  type ChainRow,
  type ExpiryMarket,
  type LiveChain,
  type LiveSide,
  type MarketPayload,
  type QuoteSide,
  type ResearchBrief,
  type SymbolMarket,
} from "../api";
import { Legend, LineChart, PairedColumns } from "../charts";
import { compact, dateTimeIst, fraction, num, pct, shortDate } from "../format";
import { Empty, Panel, Select, Status, Tile, type Tone } from "../ui";

const CALLS = "var(--series-1)";
const PUTS = "var(--series-2)";
const OI_CHART_STRIKES = 31;
const FLAT_TERM_SLOPE = 0.0025;
const LIVE_POLL_MS = 2000;

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

type Moneyness = "ITM" | "ATM" | "OTM";

const CHAIN_HEADS = ["OI", "OI chg", "Vol", "IV", "Delta", "Bid", "LTP", "Ask"];

function moneyness(strike: number, atm: number, isCall: boolean): Moneyness {
  if (strike === atm) return "ATM";
  return strike < atm === isCall ? "ITM" : "OTM";
}

function signedCompact(value: number | null | undefined): string {
  if (value === null || value === undefined) return "–";
  return `${value > 0 ? "+" : ""}${compact(value)}`;
}

function changeTone(value: number | null | undefined): string {
  return !value ? "" : value > 0 ? "pnl-up" : "pnl-down";
}

function Side({
  side,
  cls,
  state,
  maxOi,
  peak,
}: {
  side: QuoteSide | null;
  cls: "call" | "put";
  state: Moneyness;
  maxOi: number;
  peak: boolean;
}) {
  const oiShare = side?.oi && maxOi > 0 ? (side.oi / maxOi) * 100 : 0;
  const cells = [
    {
      head: "OI",
      text: side ? compact(side.oi) : "–",
      className: `oi-bar${peak ? " oi-peak" : ""}`,
      style: { "--bar": `${oiShare}%` } as CSSProperties,
    },
    { head: "OI chg", text: signedCompact(side?.oi_change), className: changeTone(side?.oi_change) },
    { head: "Vol", text: side ? compact(side.volume) : "–" },
    { head: "IV", text: side?.iv == null ? "–" : (side.iv * 100).toFixed(1) },
    { head: "Delta", text: side ? num(side.delta, 2) : "–" },
    { head: "Bid", text: side ? num(side.bid, 2) : "–" },
    { head: "LTP", text: side ? num(side.ltp, 2) : "–", className: "chain-ltp" },
    { head: "Ask", text: side ? num(side.ask, 2) : "–" },
  ];
  const ordered = cls === "call" ? cells : [...cells].reverse();
  return (
    <>
      {ordered.map((cell) => (
        <td key={cell.head} className={`right ${cls} ${state.toLowerCase()} ${cell.className ?? ""}`} style={cell.style}>
          {cell.text}
        </td>
      ))}
    </>
  );
}

function Tag({ state }: { state: Moneyness }) {
  return <span className={`moneyness ${state.toLowerCase()}`}>{state}</span>;
}

function ChainTable({ expiry, spot }: { expiry: ExpiryMarket; spot: number }) {
  const wrap = useRef<HTMLDivElement>(null);
  const atm = expiry.rows.reduce(
    (best, row) => (Math.abs(row.strike - spot) < Math.abs(best - spot) ? row.strike : best),
    expiry.rows[0]?.strike ?? 0,
  );
  const allOi = expiry.rows.flatMap((row) => [row.ce?.oi ?? 0, row.pe?.oi ?? 0]);
  const maxOi = Math.max(...allOi, 0);
  const peakCall = Math.max(...expiry.rows.map((row) => row.ce?.oi ?? 0), 0);
  const peakPut = Math.max(...expiry.rows.map((row) => row.pe?.oi ?? 0), 0);
  const spotIndex = expiry.rows.findIndex((row) => row.strike > spot);
  const spotAt = spotIndex < 0 ? expiry.rows.length : spotIndex;

  // Opens centred on the money. The caller remounts this for each symbol and expiry.
  useLayoutEffect(() => {
    const box = wrap.current;
    const row = box?.querySelector<HTMLElement>("tr.atm");
    if (!box || !row) return;
    const offset = row.getBoundingClientRect().top - box.getBoundingClientRect().top;
    box.scrollTop = offset - (box.clientHeight - row.offsetHeight) / 2;
    box.scrollLeft = (box.scrollWidth - box.clientWidth) / 2;
  }, []);

  const spotRow = (
    <tr className="spot-row" key="spot">
      <td colSpan={CHAIN_HEADS.length * 2 + 1}>
        <span>Spot {num(spot, 2)}</span>
      </td>
    </tr>
  );
  return (
    <div className="table-wrap chain-wrap" ref={wrap}>
      <table className="table table-chain">
        <thead>
          <tr className="band">
            <th colSpan={CHAIN_HEADS.length} className="band-call">
              Calls
            </th>
            <th />
            <th colSpan={CHAIN_HEADS.length} className="band-put">
              Puts
            </th>
          </tr>
          <tr>
            {CHAIN_HEADS.map((head) => (
              <th key={`c-${head}`} className="right">
                {head}
              </th>
            ))}
            <th className="strike">Strike</th>
            {[...CHAIN_HEADS].reverse().map((head) => (
              <th key={`p-${head}`} className="right">
                {head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {expiry.rows.flatMap((row, index) => {
            const callState = moneyness(row.strike, atm, true);
            const putState = moneyness(row.strike, atm, false);
            const line = (
              <tr key={row.strike} className={row.strike === atm ? "atm" : ""}>
                <Side
                  side={row.ce}
                  cls="call"
                  state={callState}
                  maxOi={maxOi}
                  peak={peakCall > 0 && row.ce?.oi === peakCall}
                />
                <td className="strike">
                  <span className="strike-cell">
                    <Tag state={callState} />
                    <b>{num(row.strike)}</b>
                    <Tag state={putState} />
                  </span>
                </td>
                <Side
                  side={row.pe}
                  cls="put"
                  state={putState}
                  maxOi={maxOi}
                  peak={peakPut > 0 && row.pe?.oi === peakPut}
                />
              </tr>
            );
            const atEnd = index === expiry.rows.length - 1 && spotAt === expiry.rows.length;
            if (index === spotAt) return [spotRow, line];
            return atEnd ? [line, spotRow] : [line];
          })}
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

function liveSide(base: QuoteSide | null, live: LiveSide | undefined): QuoteSide | null {
  if (!live) return base;
  // The feed has no previous-day OI, so the change is rolled forward from the last snapshot.
  const change =
    base?.oi_change != null && base.oi != null && live.oi != null
      ? base.oi_change + live.oi - base.oi
      : null;
  return { ...live, oi_change: change };
}

function withLive(expiry: ExpiryMarket, live: LiveChain): ExpiryMarket {
  const byStrike = new Map(live.rows.map((row) => [row.strike, row]));
  return {
    ...expiry,
    rows: expiry.rows.map((row) => {
      const update = byStrike.get(row.strike);
      return update
        ? { ...row, ce: liveSide(row.ce, update.ce), pe: liveSide(row.pe, update.pe) }
        : row;
    }),
  };
}

function useLiveChain(symbol: string, expiry: string | undefined): LiveChain | null {
  const [live, setLive] = useState<LiveChain | null>(null);
  useEffect(() => {
    if (!expiry) return;
    let active = true;
    const load = () => {
      if (document.visibilityState !== "visible") return;
      api
        .marketLive(symbol, expiry)
        .then((body) => active && setLive(body))
        .catch(() => active && setLive(null));
    };
    load();
    const timer = window.setInterval(load, LIVE_POLL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [symbol, expiry]);
  return live?.live && live.symbol === symbol && live.expiry === expiry ? live : null;
}

function SymbolDetail({ market, expiryKey }: { market: SymbolMarket; expiryKey: string }) {
  const expiry = market.expiries.find((e) => e.expiry === expiryKey) ?? market.expiries[0];
  const live = useLiveChain(market.symbol, expiry?.expiry);
  const chain = useMemo(() => (expiry && live ? withLive(expiry, live) : expiry), [expiry, live]);
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

      <Panel
        title={`Option chain, ${shortDate(expiry.expiry)}`}
        aside={
          <span className="chart-controls">
            <Legend
              items={[
                { name: "ITM calls", color: "var(--call-itm-key)" },
                { name: "ITM puts", color: "var(--put-itm-key)" },
                { name: "ATM", color: "var(--ink)" },
              ]}
            />
            {live ? (
              <Status tone="good">Live</Status>
            ) : (
              <span className="muted">Snapshot, every 5 min</span>
            )}
          </span>
        }
      >
        <ChainTable
          key={`${market.symbol}-${expiry.expiry}`}
          expiry={chain ?? expiry}
          spot={live?.spot ?? market.spot}
        />
        <p className="panel-note">
          ITM, ATM and OTM are measured against spot, and ATM is the nearest strike. Bars show open
          interest on one scale for both sides, and bold marks the largest on each side. IV is in %.
          Strikes within 6% of the forward.
        </p>
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

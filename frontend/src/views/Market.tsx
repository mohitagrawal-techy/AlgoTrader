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
import { savedStore } from "../saved";
import { Empty, Panel, Select, Status, Tile, type Tone } from "../ui";
import { StrikeChart, type StrikeChoice } from "./StrikeChart";

const CALLS = "var(--series-1)";
const PUTS = "var(--series-2)";
const OI_CHART_STRIKES = 31;
const FLAT_TERM_SLOPE = 0.0025;
const LIVE_POLL_MS = 2000;
const saved = savedStore("algoverse.options.v1.");

/** The option chains this browser saved last, for showing while the server is unreachable. */
export function readSavedMarket(): MarketPayload | null {
  return saved.read<MarketPayload>("market");
}

export function saveMarket(market: MarketPayload): void {
  saved.write("market", market);
}

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

// Columns from the outside in. Puts show the same columns mirrored.
const CHAIN_COLUMNS = [
  "OI",
  "OI chg",
  "Vol",
  "IV",
  "Delta",
  "Gamma",
  "Theta",
  "Vega",
  "Bid",
  "LTP",
  "Ask",
] as const;
type ChainColumn = (typeof CHAIN_COLUMNS)[number];
const CHAIN_VIEWS: Record<string, readonly ChainColumn[]> = {
  All: CHAIN_COLUMNS,
  "Prices and OI": ["OI", "OI chg", "Vol", "IV", "Delta", "Bid", "LTP", "Ask"],
  Greeks: ["IV", "Delta", "Gamma", "Theta", "Vega", "LTP"],
};

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
  columns,
  picked,
  onPick,
}: {
  side: QuoteSide | null;
  cls: "call" | "put";
  state: Moneyness;
  maxOi: number;
  peak: boolean;
  columns: readonly ChainColumn[];
  picked: boolean;
  onPick: () => void;
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
    { head: "Gamma", text: side ? num(side.gamma, 4) : "–" },
    { head: "Theta", text: side ? num(side.theta, 2) : "–" },
    { head: "Vega", text: side ? num(side.vega, 2) : "–" },
    { head: "Bid", text: side ? num(side.bid, 2) : "–" },
    { head: "LTP", text: side ? num(side.ltp, 2) : "–", className: "chain-ltp" },
    { head: "Ask", text: side ? num(side.ask, 2) : "–" },
  ];
  const shown = cells.filter((cell) => columns.includes(cell.head as ChainColumn));
  const ordered = cls === "call" ? shown : [...shown].reverse();
  return (
    <>
      {ordered.map((cell) => (
        <td
          key={cell.head}
          className={`right ${cls} ${state.toLowerCase()} pickable${picked ? " picked" : ""} ${cell.className ?? ""}`}
          style={cell.style}
          onClick={onPick}
          title={`Chart this ${cls}`}
        >
          {cell.text}
        </td>
      ))}
    </>
  );
}

function Tag({ state }: { state: Moneyness }) {
  return <span className={`moneyness ${state.toLowerCase()}`}>{state}</span>;
}

function ChainTable({
  expiry,
  spot,
  columns,
  picked,
  onPick,
}: {
  expiry: ExpiryMarket;
  spot: number;
  columns: readonly ChainColumn[];
  picked: StrikeChoice;
  onPick: (choice: StrikeChoice) => void;
}) {
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
      <td colSpan={columns.length * 2 + 1}>
        <span>Spot {num(spot, 2)}</span>
      </td>
    </tr>
  );
  return (
    <div className="table-wrap chain-wrap" ref={wrap}>
      <table className="table table-chain">
        <thead>
          <tr className="band">
            <th colSpan={columns.length} className="band-call">
              Calls
            </th>
            <th />
            <th colSpan={columns.length} className="band-put">
              Puts
            </th>
          </tr>
          <tr>
            {columns.map((head) => (
              <th key={`c-${head}`} className="right">
                {head}
              </th>
            ))}
            <th className="strike">Strike</th>
            {[...columns].reverse().map((head) => (
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
                  columns={columns}
                  peak={peakCall > 0 && row.ce?.oi === peakCall}
                  picked={picked.strike === row.strike && picked.type === "CE"}
                  onPick={() => onPick({ strike: row.strike, type: "CE" })}
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
                  columns={columns}
                  peak={peakPut > 0 && row.pe?.oi === peakPut}
                  picked={picked.strike === row.strike && picked.type === "PE"}
                  onPick={() => onPick({ strike: row.strike, type: "PE" })}
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

interface LiveState {
  chain: LiveChain | null;
  /** True when the server is unreachable and these prices are this browser's saved copy. */
  saved: boolean;
}

/**
 * Live prices for one expiry, refreshed every 2 seconds. Each update is saved in this browser,
 * one copy per underlying and expiry, and the saved copy is shown when the server is down.
 */
function useLiveChain(symbol: string, expiry: string | undefined): LiveState {
  const key = `live.${symbol}.${expiry}`;
  const [state, setState] = useState<LiveState>({ chain: null, saved: false });
  useEffect(() => {
    if (!expiry) return;
    let active = true;
    const fallback = () => {
      const copy = saved.read<LiveChain>(key);
      setState({ chain: copy, saved: copy != null });
    };
    fallback();
    const load = () => {
      if (document.visibilityState !== "visible") return;
      api
        .marketLive(symbol, expiry)
        .then((body) => {
          if (!active) return;
          if (body.live) saved.write(key, body);
          setState({ chain: body, saved: false });
        })
        .catch(() => active && fallback());
    };
    load();
    const timer = window.setInterval(load, LIVE_POLL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [symbol, expiry, key]);
  const chain = state.chain;
  const usable = chain?.live && chain.symbol === symbol && chain.expiry === expiry;
  return { chain: usable ? chain : null, saved: !!usable && state.saved };
}

const CHAIN_VIEW_KEY = "algoverse-chain-view";

function readChainView(): string {
  try {
    const saved = localStorage.getItem(CHAIN_VIEW_KEY);
    return saved && saved in CHAIN_VIEWS ? saved : "All";
  } catch {
    return "All";
  }
}

function SymbolDetail({ market, expiryKey }: { market: SymbolMarket; expiryKey: string }) {
  const expiry = market.expiries.find((e) => e.expiry === expiryKey) ?? market.expiries[0];
  const liveState = useLiveChain(market.symbol, expiry?.expiry);
  const live = liveState.chain;
  const chain = useMemo(() => (expiry && live ? withLive(expiry, live) : expiry), [expiry, live]);
  const [view, setView] = useState(readChainView);
  const [picked, setPicked] = useState<(StrikeChoice & { expiry: string }) | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const atmStrike = expiry
    ? expiry.rows.reduce(
        (best, row) =>
          Math.abs(row.strike - market.spot) < Math.abs(best - market.spot) ? row.strike : best,
        expiry.rows[0]?.strike ?? 0,
      )
    : 0;
  // Each expiry opens on its at-the-money call until a strike is picked.
  const choice: StrikeChoice =
    picked && picked.expiry === expiry?.expiry
      ? { strike: picked.strike, type: picked.type }
      : { strike: atmStrike, type: "CE" };
  const choose = (next: StrikeChoice) =>
    setPicked({ ...next, expiry: expiry?.expiry ?? "" });
  const pickFromChain = (next: StrikeChoice) => {
    choose(next);
    chartRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const chooseView = (next: string) => {
    setView(next);
    try {
      localStorage.setItem(CHAIN_VIEW_KEY, next);
    } catch {
      // The choice still applies for this visit when storage is blocked.
    }
  };
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
            <Select
              label="Columns"
              value={view}
              onChange={chooseView}
              options={Object.keys(CHAIN_VIEWS).map((name) => ({ value: name, label: name }))}
            />
            {live && liveState.saved ? (
              <Status tone="warning">
                Saved {live.as_of ? dateTimeIst(live.as_of) : "copy"}
              </Status>
            ) : live ? (
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
          columns={CHAIN_VIEWS[view] ?? CHAIN_COLUMNS}
          picked={choice}
          onPick={pickFromChain}
        />
        <p className="panel-note">
          ITM, ATM and OTM are measured against spot, and ATM is the nearest strike. Bars show open
          interest on one scale for both sides, and bold marks the largest on each side. IV is in %.
          Theta is rupees per unit per day, vega is rupees per unit per volatility point, and gamma
          is the change in delta for a one-point move.
          Strikes within 6% of the forward. Click a call or put to chart it below.
        </p>
      </Panel>

      {expiry.rows.length > 0 && (
        <StrikeChart
          key={`${market.symbol}-${expiry.expiry}`}
          ref={chartRef}
          symbol={market.symbol}
          expiry={expiry.expiry}
          fallbackStrikes={expiry.rows.map((row) => row.strike)}
          choice={choice}
          onChoose={choose}
        />
      )}
    </>
  );
}

export function MarketView({ market, offline = false }: { market: MarketPayload; offline?: boolean }) {
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
      {offline && (
        <div className="banner banner-warning">
          <Status tone="warning">Offline</Status> The server is unreachable. Showing the option
          chains this browser saved
          {market.generated_at ? ` at ${dateTimeIst(market.generated_at)}` : ""}.
        </div>
      )}
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

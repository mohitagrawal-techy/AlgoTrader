import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type FocusEvent,
  type PointerEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { api, type IndexDetails } from "../api";
import { HEAT_SCALE, heatClass } from "../charts";
import { num, shortDate } from "../format";

const SHOW_DELAY_MS = 180;
const HIDE_DELAY_MS = 160;
const CACHE_MS = 20000;
const GAP = 10;
const EDGE = 8;
const SPARK_WIDTH = 330;
const SPARK_HEIGHT = 44;
const TOP_MOVERS = 3;
const TILE_HEIGHT = 60;
const MIN_SPACE_BELOW = 320;
const PERIODS = ["1W", "1M", "3M", "1Y"];

function signed(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "–";
  const rounded = Number(value.toFixed(digits));
  return `${rounded > 0 ? "+" : ""}${(rounded === 0 ? 0 : rounded).toFixed(digits)}%`;
}

function tone(value: number | null | undefined): string {
  return !value ? "" : value > 0 ? "pnl-up" : "pnl-down";
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function Spark({ closes }: { closes: number[] }) {
  if (closes.length < 2) return null;
  const low = Math.min(...closes);
  const span = Math.max(...closes) - low || 1;
  const points = closes.map((close, index) => ({
    x: 6 + (index / (closes.length - 1)) * (SPARK_WIDTH - 12),
    y: SPARK_HEIGHT - 7 - ((close - low) / span) * (SPARK_HEIGHT - 14),
  }));
  return (
    <svg
      className="index-spark"
      viewBox={`0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}`}
      role="img"
      aria-label="Closing price over the last five sessions"
    >
      <polyline
        points={points.map((point) => `${point.x},${point.y}`).join(" ")}
        fill="none"
        stroke="var(--series-1)"
        strokeWidth={2}
        strokeLinejoin="round"
      />
      {points.map((point) => (
        <circle key={point.x} cx={point.x} cy={point.y} r={3} fill="var(--series-1)" stroke="var(--surface)" />
      ))}
    </svg>
  );
}

function Members({ data }: { data: NonNullable<IndexDetails["constituents"]> }) {
  if (data.priced === 0) {
    return <p className="secondary">{data.count} stocks listed. Prices are unavailable right now.</p>;
  }
  const gainers = data.members.slice(0, TOP_MOVERS);
  const losers = data.members.slice(-TOP_MOVERS).reverse();
  return (
    <>
      <div className="index-member-summary">
        {data.count} stocks: {data.advancing} up, {data.declining} down, average{" "}
        <span className={tone(data.average_change)}>{signed(data.average_change)}</span>
      </div>
      <div className="segments thin" role="img" aria-label={`${data.advancing} advancing, ${data.declining} declining`}>
        <i className="bar-up" style={{ flexGrow: data.advancing }} />
        <i className="bar-down" style={{ flexGrow: data.declining }} />
      </div>
      <dl className="index-facts" style={{ marginTop: 6 }}>
        {gainers.map((member) => (
          <Fact label={member.symbol} key={member.symbol}>
            <span className={tone(member.change_pct)}>{signed(member.change_pct)}</span>
          </Fact>
        ))}
        {losers.map((member) => (
          <Fact label={member.symbol} key={`l-${member.symbol}`}>
            <span className={tone(member.change_pct)}>{signed(member.change_pct)}</span>
          </Fact>
        ))}
      </dl>
      <details>
        <summary className="secondary">All {data.priced} stocks</summary>
        <div className="index-member-list">
          <table className="index-members">
            <tbody>
              {data.members.map((member) => (
                <tr key={member.symbol} title={`${member.name}, ${member.industry}`}>
                  <td>{member.symbol}</td>
                  <td>{num(member.last, 2)}</td>
                  <td className={tone(member.change_pct)}>{signed(member.change_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <p className="secondary" style={{ marginTop: 4 }}>
        NSE's list has no weights, so the average counts every stock equally.
      </p>
    </>
  );
}

function CardBody({ data }: { data: IndexDetails }) {
  const { stats } = data;
  return (
    <>
      <div className="index-card-head">
        <span className="index-card-title">{data.label}</span>
        {data.quote_day && <span className="muted">{shortDate(data.quote_day)}</span>}
      </div>
      <div className="index-card-price">
        <b>{num(data.last, 2)}</b>
        <span className={tone(data.change_pct)}>{signed(data.change_pct)}</span>
        <span className="muted">{num(data.change, 2)}</span>
      </div>

      <h4>Today</h4>
      <dl className="index-facts">
        <Fact label="Open">{num(data.open, 2)}</Fact>
        <Fact label="Prev close">{num(data.previous_close, 2)}</Fact>
        <Fact label="High">{num(data.high, 2)}</Fact>
        <Fact label="Low">{num(data.low, 2)}</Fact>
        {data.vs_nifty !== null && data.name !== "Nifty 50" && (
          <Fact label="vs Nifty 50">
            <span className={tone(data.vs_nifty)}>{signed(data.vs_nifty)}</span>
          </Fact>
        )}
      </dl>

      {data.five_days.length > 0 && (
        <>
          <h4>Last 5 sessions</h4>
          <Spark closes={data.five_days.map((day) => day.close)} />
          <table className="index-days">
            <tbody>
              {[...data.five_days].reverse().map((day) => (
                <tr key={day.date}>
                  <td>{shortDate(day.date)}</td>
                  <td>{num(day.close, 2)}</td>
                  <td>
                    {day.change_pct === null ? (
                      "–"
                    ) : (
                      <span className={`change heat-${heatClass(day.change_pct)}`}>{signed(day.change_pct)}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {Object.keys(data.returns).length > 0 && (
        <>
          <h4>Returns</h4>
          <div className="index-periods">
            {PERIODS.map((period) => {
              const value = data.returns[period];
              return (
                <div key={period}>
                  <small>{period}</small>
                  <span className={value === undefined ? "" : `heat-${heatClass(value / HEAT_SCALE[period])}`}>
                    {signed(value, 1)}
                  </span>
                </div>
              );
            })}
          </div>
        </>
      )}

      {stats.high_52w !== null && (
        <>
          <h4>Levels</h4>
          <dl className="index-facts">
            <Fact label="52W high">{num(stats.high_52w, 0)}</Fact>
            <Fact label="From high">
              <span className={tone(stats.from_high_pct)}>{signed(stats.from_high_pct, 1)}</span>
            </Fact>
            <Fact label="52W low">{num(stats.low_52w, 0)}</Fact>
            <Fact label="From low">
              <span className={tone(stats.from_low_pct)}>{signed(stats.from_low_pct, 1)}</span>
            </Fact>
            <Fact label="EMA 20">{num(stats.ema20, 0)}</Fact>
            <Fact label="EMA 50">{num(stats.ema50, 0)}</Fact>
            {stats.realised_vol !== null && <Fact label="20D vol">{stats.realised_vol.toFixed(1)}%</Fact>}
            {stats.average_range_pct !== null && (
              <Fact label="Avg range">{stats.average_range_pct.toFixed(2)}%</Fact>
            )}
          </dl>
        </>
      )}

      {data.has_constituents && (
        <>
          <h4>Constituents</h4>
          {data.constituents ? (
            <Members data={data.constituents} />
          ) : (
            <p className="secondary">Not added yet. Add this index's list in the Index constituents panel.</p>
          )}
        </>
      )}
    </>
  );
}

function triggerOf(target: EventTarget | null): HTMLElement | null {
  return target instanceof Element ? target.closest<HTMLElement>("[data-index-name]") : null;
}

/** Shows an index's quick view when any element marked with `data-index-name` is hovered. */
export function IndexHoverLayer({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<{ name: string; anchor: DOMRect } | null>(null);
  const [details, setDetails] = useState<IndexDetails | null>(null);
  const [failed, setFailed] = useState(false);
  const [position, setPosition] = useState<CSSProperties>({ left: EDGE, top: EDGE });
  const card = useRef<HTMLDivElement>(null);
  const showTimer = useRef<number | undefined>(undefined);
  const hideTimer = useRef<number | undefined>(undefined);
  const cache = useRef(new Map<string, { at: number; data: IndexDetails }>());
  const pointerType = useRef("mouse");
  const openName = useRef<string | null>(null);
  openName.current = open?.name ?? null;

  const close = useCallback(() => {
    window.clearTimeout(showTimer.current);
    window.clearTimeout(hideTimer.current);
    setOpen(null);
  }, []);
  const keepOpen = useCallback(() => window.clearTimeout(hideTimer.current), []);
  const scheduleHide = useCallback(() => {
    window.clearTimeout(showTimer.current);
    window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => setOpen(null), HIDE_DELAY_MS);
  }, []);
  const show = useCallback(
    (element: HTMLElement, delay: number) => {
      const name = element.dataset.indexName as string;
      keepOpen();
      window.clearTimeout(showTimer.current);
      if (openName.current === name) return;
      showTimer.current = window.setTimeout(
        () => setOpen({ name, anchor: element.getBoundingClientRect() }),
        delay,
      );
    },
    [keepOpen],
  );

  useEffect(() => {
    if (!open) return;
    const saved = cache.current.get(open.name);
    setFailed(false);
    if (saved && Date.now() - saved.at < CACHE_MS) {
      setDetails(saved.data);
      return;
    }
    setDetails(null);
    let active = true;
    api
      .overviewDetails(open.name)
      .then((data) => {
        cache.current.set(open.name, { at: Date.now(), data });
        if (active) setDetails(data);
      })
      .catch(() => active && setFailed(true));
    return () => {
      active = false;
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !card.current) return;
    card.current.style.maxHeight = "";
    const { width, height } = card.current.getBoundingClientRect();
    const { anchor } = open;
    const spaceBelow = window.innerHeight - anchor.bottom - GAP - EDGE;
    if (anchor.height > TILE_HEIGHT && spaceBelow >= MIN_SPACE_BELOW) {
      // Beside a tile the card would cover the next tile in the row, so it opens underneath.
      const left = Math.max(EDGE, Math.min(anchor.left, window.innerWidth - width - EDGE));
      setPosition({ left, top: anchor.bottom + GAP, maxHeight: spaceBelow });
      return;
    }
    let left = anchor.right + GAP;
    if (left + width > window.innerWidth - EDGE) left = anchor.left - width - GAP;
    if (left < EDGE) left = Math.max(EDGE, Math.min(anchor.left, window.innerWidth - width - EDGE));
    const top = Math.max(EDGE, Math.min(anchor.top, window.innerHeight - height - EDGE));
    setPosition({ left, top });
  }, [open, details, failed]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && close();
    const onScroll = (event: Event) => {
      if (!(event.target instanceof Node && card.current?.contains(event.target))) close();
    };
    const onDown = (event: globalThis.PointerEvent) => {
      const target = event.target as Node;
      if (!card.current?.contains(target) && !triggerOf(event.target)) close();
    };
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open, close]);

  useEffect(() => close, [close]);

  const onPointerOver = (event: PointerEvent<HTMLDivElement>) => {
    if (event.pointerType === "touch") return;
    const element = triggerOf(event.target);
    if (element) show(element, SHOW_DELAY_MS);
    else scheduleHide();
  };
  const onClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (pointerType.current !== "touch") return;
    const element = triggerOf(event.target);
    if (!element) return;
    if (openName.current === element.dataset.indexName) close();
    else show(element, 0);
  };
  const onFocus = (event: FocusEvent<HTMLDivElement>) => {
    const element = triggerOf(event.target);
    if (element?.matches(":focus-visible")) show(element, 0);
  };

  return (
    <>
      <div
        className="index-hover-layer"
        onPointerOver={onPointerOver}
        onPointerLeave={scheduleHide}
        onPointerDown={(event) => {
          pointerType.current = event.pointerType;
        }}
        onClick={onClick}
        onFocus={onFocus}
        onBlur={scheduleHide}
      >
        {children}
      </div>
      {open &&
        createPortal(
          <div
            className="index-card"
            ref={card}
            role="dialog"
            aria-label="Index details"
            style={position}
            onPointerEnter={keepOpen}
            onPointerLeave={scheduleHide}
          >
            {details ? (
              <CardBody data={details} />
            ) : (
              <p className="secondary">{failed ? "Could not load details." : "Loading…"}</p>
            )}
          </div>,
          document.body,
        )}
    </>
  );
}

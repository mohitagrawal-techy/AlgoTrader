import {
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { TuneNode, TuneSession } from "../api";
import { niceTicks } from "../charts";
import { num, signedInr } from "../format";
import { Segmented, Select } from "../components/ui";
import { settingName, settingValue } from "./tuneSettings";

type Metric = "sharpe" | "net_pnl";
const METRICS = [
  { value: "sharpe" as const, label: "Sharpe" },
  { value: "net_pnl" as const, label: "Net P&L" },
];
const HEIGHT = 240;
const MARGIN = { top: 14, right: 16, bottom: 34, left: 62 };

interface Dot {
  node: TuneNode;
  x: number;
  y: number;
  /** "start" for the starting version, else whether the change was kept. */
  state: "start" | "kept" | "dropped";
}

function read(summary: TuneNode["validate"], metric: Metric): number | null {
  const value = summary[metric];
  return value === null || value === undefined || !Number.isFinite(value)
    ? null
    : value;
}

function show(value: number, metric: Metric): string {
  return metric === "sharpe" ? num(value, 2) : signedInr(value);
}

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(entry.contentRect.width),
    );
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

function padded(values: number[], include: number[] = []): [number, number] {
  const all = [...values, ...include];
  let low = Math.min(...all);
  let high = Math.max(...all);
  if (low === high) {
    low -= Math.abs(low) * 0.1 || 1;
    high += Math.abs(high) * 0.1 || 1;
  }
  const pad = (high - low) * 0.08;
  return [low - pad, high + pad];
}

/** A scatter plot of tuning results. Dots are buttons that open the comparison. */
function Plot({
  label,
  dots,
  xDomain,
  yDomain,
  xTicks,
  xTick,
  xTitle,
  yTitle,
  metric,
  selectedId,
  onSelect,
  under,
  over,
}: {
  label: string;
  dots: Dot[];
  xDomain: [number, number];
  yDomain: [number, number];
  xTicks: number[];
  xTick: (value: number) => string;
  xTitle: string;
  yTitle: string;
  metric: Metric;
  selectedId: string | null;
  onSelect: (node: TuneNode) => void;
  /** Drawn under the dots, given the scales. */
  under?: (x: (v: number) => number, y: (v: number) => number) => ReactNode;
  over?: (x: (v: number) => number, y: (v: number) => number) => ReactNode;
}) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<Dot | null>(null);
  const clip = useId();
  const inner = Math.max(width - MARGIN.left - MARGIN.right, 10);
  const tall = HEIGHT - MARGIN.top - MARGIN.bottom;
  const x = (v: number) =>
    MARGIN.left + ((v - xDomain[0]) / (xDomain[1] - xDomain[0])) * inner;
  const y = (v: number) =>
    MARGIN.top + (1 - (v - yDomain[0]) / (yDomain[1] - yDomain[0])) * tall;
  const yTicks = niceTicks(yDomain[0], yDomain[1], 5);
  const focus = hover ?? dots.find((d) => d.node.id === selectedId) ?? null;
  return (
    <div className="tune-plot">
      <div ref={ref} style={{ height: HEIGHT }}>
        {width > 0 && (
          <svg width={width} height={HEIGHT} role="img" aria-label={label}>
            <defs>
              <clipPath id={clip}>
                <rect
                  x={MARGIN.left}
                  y={MARGIN.top}
                  width={inner}
                  height={tall}
                />
              </clipPath>
            </defs>
            {yTicks.map((tick) => (
              <g key={`y${tick}`}>
                <line
                  x1={MARGIN.left}
                  x2={MARGIN.left + inner}
                  y1={y(tick)}
                  y2={y(tick)}
                  className={tick === 0 ? "tune-plot-zero" : "tune-plot-grid"}
                />
                <text
                  x={MARGIN.left - 8}
                  y={y(tick)}
                  dy="0.32em"
                  textAnchor="end"
                  className="tune-plot-tick"
                >
                  {metric === "sharpe" ? num(tick, 1) : compactInr(tick)}
                </text>
              </g>
            ))}
            {xTicks.map((tick) => (
              <text
                key={`x${tick}`}
                x={x(tick)}
                y={MARGIN.top + tall + 16}
                textAnchor="middle"
                className="tune-plot-tick"
              >
                {xTick(tick)}
              </text>
            ))}
            <text
              x={MARGIN.left + inner / 2}
              y={HEIGHT - 2}
              textAnchor="middle"
              className="tune-plot-title"
            >
              {xTitle}
            </text>
            <text
              transform={`translate(12 ${MARGIN.top + tall / 2}) rotate(-90)`}
              textAnchor="middle"
              className="tune-plot-title"
            >
              {yTitle}
            </text>
            <g clipPath={`url(#${clip})`}>
              {under?.(x, y)}
              {dots.map((dot) => {
                const cx = x(dot.x);
                const cy = y(dot.y);
                const selected = dot.node.id === selectedId;
                const name =
                  dot.state === "start"
                    ? `Start version, ${show(dot.y, metric)}`
                    : `${dot.node.label}, ${dot.state === "kept" ? "kept" : "not kept"}, ${show(dot.y, metric)}`;
                return (
                  <g
                    key={dot.node.id}
                    role="button"
                    tabIndex={0}
                    aria-label={name}
                    className={`tune-dot ${dot.state}${selected ? " selected" : ""}`}
                    onMouseEnter={() => setHover(dot)}
                    onMouseLeave={() => setHover(null)}
                    onFocus={() => setHover(dot)}
                    onBlur={() => setHover(null)}
                    onClick={() => onSelect(dot.node)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onSelect(dot.node);
                      }
                    }}
                  >
                    <title>{name}</title>
                    {dot.state === "dropped" ? (
                      <rect
                        x={cx - 4}
                        y={cy - 4}
                        width={8}
                        height={8}
                        transform={`rotate(45 ${cx} ${cy})`}
                      />
                    ) : (
                      <circle
                        cx={cx}
                        cy={cy}
                        r={dot.state === "kept" ? 6 : 5}
                      />
                    )}
                  </g>
                );
              })}
              {over?.(x, y)}
            </g>
          </svg>
        )}
      </div>
      <p className="tune-plot-readout" aria-live="polite">
        {focus
          ? `${focus.state === "start" ? "Start version" : focus.node.label} · ${yTitle} ${show(focus.y, metric)} · ${focus.node.validate.trades} unseen trades`
          : "Point at a dot to read it; choose one to compare it with the version it came from."}
      </p>
    </div>
  );
}

function compactInr(value: number): string {
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (abs >= 1e7) return `${sign}₹${num(abs / 1e7, 1)}Cr`;
  if (abs >= 1e5) return `${sign}₹${num(abs / 1e5, 1)}L`;
  if (abs >= 1e3) return `${sign}₹${num(abs / 1e3, 0)}k`;
  return `${sign}₹${num(abs, 0)}`;
}

function Key() {
  return (
    <div className="tune-legend" aria-hidden>
      <span>
        <i className="tune-legend-dot kept" />
        Kept
      </span>
      <span>
        <i className="tune-legend-dot dropped" />
        Not kept
      </span>
      <span>
        <i className="tune-legend-dot start" />
        Start version
      </span>
      <span>
        <i className="tune-legend-line" />
        Version being tuned
      </span>
    </div>
  );
}

/** Progress, overfitting and sensitivity charts for one tuning session. */
export function TuneCharts({
  session,
  labels,
  selectedId,
  onSelect,
}: {
  session: TuneSession;
  /** Readable names of settings, by path. */
  labels: Record<string, string>;
  selectedId: string | null;
  onSelect: (node: TuneNode) => void;
}) {
  const [metric, setMetric] = useState<Metric>("sharpe");
  const root = session.nodes[session.root_id];
  const roundOf = useMemo(() => {
    const map = new Map<string, number>();
    for (const round of session.rounds) {
      for (const id of round.tried) map.set(id, round.round);
    }
    return map;
  }, [session]);
  const tried = Object.values(session.nodes).filter(
    (node) => node.depth > 0 && roundOf.has(node.id),
  );
  const paths = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of tried) {
      if (!node.path || settingValue(node, node.path, "new") === null) continue;
      counts.set(node.path, (counts.get(node.path) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [tried]); // eslint-disable-line react-hooks/exhaustive-deps
  const [path, setPath] = useState<string>("");
  const setting = paths.some(([p]) => p === path)
    ? path
    : (paths[0]?.[0] ?? "");

  if (!root || tried.length === 0) return null;
  const state = (node: TuneNode): Dot["state"] =>
    node.depth === 0 ? "start" : node.accepted ? "kept" : "dropped";
  const unseen = (node: TuneNode) => read(node.validate, metric);
  const yTitle = metric === "sharpe" ? "Unseen Sharpe" : "Unseen net P&L";

  // 1. Progress: each change at its round, spread across the round's width.
  const lastRound = Math.max(...session.rounds.map((r) => r.round));
  const progress: Dot[] = [];
  for (const round of session.rounds) {
    round.tried.forEach((id, index) => {
      const node = session.nodes[id];
      const value = node ? unseen(node) : null;
      if (!node || value === null) return;
      const spread =
        round.tried.length > 1 ? index / (round.tried.length - 1) - 0.5 : 0;
      progress.push({
        node,
        x: round.round + spread * 0.6,
        y: value,
        state: state(node),
      });
    });
  }
  const startValue = unseen(root);
  const steps: [number, number][] = [];
  for (const round of session.rounds) {
    const parent = session.nodes[round.parent_id];
    const level = parent ? unseen(parent) : null;
    if (level === null) continue;
    steps.push([round.round - 0.5, level], [round.round + 0.5, level]);
  }
  const best = session.nodes[session.best_id];
  const bestValue = best ? unseen(best) : null;
  if (bestValue !== null && steps.length)
    steps.push([lastRound + 0.5, bestValue]);
  const progressY = padded(
    progress.map((d) => d.y),
    [...steps.map(([, v]) => v), ...(startValue === null ? [] : [startValue])],
  );
  const roundTicks = session.rounds.map((r) => r.round);

  // 2. Overfitting: design result across, unseen result up.
  const fit: Dot[] = [root, ...tried].flatMap((node) => {
    const across = read(node.design, metric);
    const up = unseen(node);
    return across === null || up === null
      ? []
      : [{ node, x: across, y: up, state: state(node) }];
  });
  const fitX = padded(
    fit.map((d) => d.x),
    [0],
  );
  const fitY = padded(
    fit.map((d) => d.y),
    [0],
  );

  // 3. Sensitivity: the chosen setting's value across, unseen result up.
  const sensitivity: Dot[] = tried.flatMap((node) => {
    if (node.path !== setting) return [];
    const value = settingValue(node, setting, "new");
    const up = unseen(node);
    return value === null || up === null
      ? []
      : [{ node, x: value, y: up, state: state(node) }];
  });
  const firstChange = tried.find((node) => node.path === setting);
  const startSetting = firstChange
    ? settingValue(firstChange, setting, "old")
    : null;
  if (startSetting !== null && startValue !== null) {
    sensitivity.push({
      node: root,
      x: startSetting,
      y: startValue,
      state: "start",
    });
  }
  sensitivity.sort((a, b) => a.x - b.x);
  const sensX = padded(sensitivity.map((d) => d.x));
  const sensY = padded(sensitivity.map((d) => d.y));
  const pathLabel = (p: string) => settingName(p, labels);

  return (
    <div className="tune-charts">
      <div className="tune-charts-bar">
        <Segmented
          label="Result to plot"
          value={metric}
          options={METRICS}
          onChange={setMetric}
        />
        <Key />
      </div>
      <section aria-label="Progress by round">
        <h3 className="tune-chart-title">
          Progress: every change by round
          <small className="secondary">
            {" "}
            A flat line under a cloud of grey dots means tuning has stopped
            finding anything.
          </small>
        </h3>
        <Plot
          label={`${yTitle} of every change tried, by round, with the version being tuned`}
          dots={progress}
          xDomain={[0.5, lastRound + 0.5]}
          yDomain={progressY}
          xTicks={roundTicks}
          xTick={(v) => `Round ${v}`}
          xTitle="Round"
          yTitle={yTitle}
          metric={metric}
          selectedId={selectedId}
          onSelect={onSelect}
          under={(x, y) => (
            <>
              {startValue !== null && (
                <line
                  x1={x(0.5)}
                  x2={x(lastRound + 0.5)}
                  y1={y(startValue)}
                  y2={y(startValue)}
                  className="tune-plot-start"
                />
              )}
              {steps.length > 0 && (
                <polyline
                  points={steps
                    .map(([sx, sy], i) =>
                      i > 0 && steps[i - 1][1] !== sy
                        ? `${x(sx)},${y(steps[i - 1][1])} ${x(sx)},${y(sy)}`
                        : `${x(sx)},${y(sy)}`,
                    )
                    .join(" ")}
                  className="tune-plot-best"
                />
              )}
            </>
          )}
        />
      </section>
      <div className="tune-charts-pair">
        <section aria-label="Design against unseen">
          <h3 className="tune-chart-title">
            Overfitting check
            <small className="secondary">
              {" "}
              Far right but low: it only worked on the design sessions.
            </small>
          </h3>
          <Plot
            label={`Design ${METRICS.find((m) => m.value === metric)?.label} against ${yTitle} for each version`}
            dots={fit}
            xDomain={fitX}
            yDomain={fitY}
            xTicks={niceTicks(fitX[0], fitX[1], 4)}
            xTick={(v) => (metric === "sharpe" ? num(v, 1) : compactInr(v))}
            xTitle={`Design ${metric === "sharpe" ? "Sharpe" : "net P&L"}`}
            yTitle={yTitle}
            metric={metric}
            selectedId={selectedId}
            onSelect={onSelect}
            under={(x, y) => (
              <>
                <line
                  x1={x(0)}
                  x2={x(0)}
                  y1={y(fitY[0])}
                  y2={y(fitY[1])}
                  className="tune-plot-zero"
                />
                {metric === "sharpe" && (
                  <line
                    x1={x(Math.max(fitX[0], fitY[0]))}
                    y1={y(Math.max(fitX[0], fitY[0]))}
                    x2={x(Math.min(fitX[1], fitY[1]))}
                    y2={y(Math.min(fitX[1], fitY[1]))}
                    className="tune-plot-start"
                  />
                )}
              </>
            )}
          />
        </section>
        <section aria-label="Sensitivity of one setting">
          <h3 className="tune-chart-title tune-chart-title-row">
            <span>
              Sensitivity
              <small className="secondary">
                {" "}
                A broad hump holds up; a lone spike is luck.
              </small>
            </span>
            {paths.length > 0 && (
              <Select
                small
                aria-label="Setting"
                value={setting}
                onChange={(event) => setPath(event.target.value)}
              >
                {paths.map(([p, count]) => (
                  <option key={p} value={p}>
                    {pathLabel(p)} ({count})
                  </option>
                ))}
              </Select>
            )}
          </h3>
          {sensitivity.length > 1 ? (
            <Plot
              label={`${yTitle} across the values tried for ${setting}`}
              dots={sensitivity}
              xDomain={sensX}
              yDomain={sensY}
              xTicks={niceTicks(sensX[0], sensX[1], 5)}
              xTick={(v) => num(v, Math.abs(v) < 10 ? 2 : 0)}
              xTitle={pathLabel(setting)}
              yTitle={yTitle}
              metric={metric}
              selectedId={selectedId}
              onSelect={onSelect}
              under={(x, y) => (
                <polyline
                  points={sensitivity
                    .map((d) => `${x(d.x)},${y(d.y)}`)
                    .join(" ")}
                  className="tune-plot-sens"
                />
              )}
            />
          ) : (
            <p className="secondary tune-plot-empty">
              Too few values of one setting to draw a curve yet. Tune a single
              setting over a few rounds to see one.
            </p>
          )}
        </section>
      </div>
    </div>
  );
}

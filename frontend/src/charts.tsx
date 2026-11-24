import {
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";

const MARGIN = { top: 22, right: 18, bottom: 28, left: 58 };

/** The chart's drawn size. A resizable panel can make it differ from the preferred height. */
function useSize(preferredHeight: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useLayoutEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) =>
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height }),
    );
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, size.width, size.height || preferredHeight] as const;
}

function chartStyle(preferredHeight: number): CSSProperties {
  return { height: preferredHeight, "--chart-height": `${preferredHeight}px` } as CSSProperties;
}

export function niceTicks(min: number, max: number, count = 5): number[] {
  if (min === max) return [min];
  const rough = (max - min) / count;
  const power = 10 ** Math.floor(Math.log10(rough));
  const ratio = rough / power;
  const step = (ratio >= 5 ? 10 : ratio >= 2 ? 5 : ratio >= 1.5 ? 2 : 1) * power;
  const ticks: number[] = [];
  for (let tick = Math.ceil(min / step) * step; tick <= max + step * 1e-6; tick += step) {
    ticks.push(Number(tick.toFixed(10)));
  }
  return ticks;
}

const IST_OFFSET_MS = 5.5 * 3600000;
// Up to a month for intraday and recent charts, then quarters to five years for long histories.
const TIME_STEPS_MINUTES = [
  1, 5, 10, 15, 30, 60, 120, 240, 1440, 2880, 7200, 10080, 43200, 131400, 262800, 525600, 1051200,
  2628000,
];

/** Ticks on round minutes and days of the IST clock. */
export function timeTicks(min: number, max: number, count = 5): number[] {
  const minutes =
    TIME_STEPS_MINUTES.find((step) => (max - min) / (step * 60000) <= count) ??
    TIME_STEPS_MINUTES[TIME_STEPS_MINUTES.length - 1];
  const step = minutes * 60000;
  const ticks: number[] = [];
  for (
    let tick = Math.ceil((min + IST_OFFSET_MS) / step) * step - IST_OFFSET_MS;
    tick <= max;
    tick += step
  ) {
    ticks.push(tick);
  }
  return ticks;
}

function linear(domain: [number, number], range: [number, number]) {
  const span = domain[1] - domain[0] || 1;
  return (value: number) => range[0] + ((value - domain[0]) / span) * (range[1] - range[0]);
}

function Tooltip({ x, width, children }: { x: number; width: number; children: ReactNode }) {
  const flip = x > width * 0.62;
  return (
    <div
      className="chart-tooltip"
      style={flip ? { right: width - x + 12, top: 12 } : { left: x + 12, top: 12 }}
    >
      {children}
    </div>
  );
}

export function Legend({ items }: { items: { name: string; color: string }[] }) {
  return (
    <div className="legend">
      {items.map((item) => (
        <span key={item.name}>
          <i style={{ background: item.color }} />
          {item.name}
        </span>
      ))}
    </div>
  );
}

export interface Series {
  name: string;
  color: string;
  points: { x: number; y: number | null }[];
}

interface LineChartProps {
  series: Series[];
  height?: number;
  label: string;
  formatX: (value: number) => string;
  formatY: (value: number) => string;
  formatTipX?: (value: number) => string;
  formatTipY?: (value: number) => string;
  markers?: { x: number; label: string }[];
  /** Shades area above zero and below zero with the two diverging hues. */
  polarity?: boolean;
  dots?: boolean;
  includeZero?: boolean;
  /** Smallest y range to draw, so nearly flat data looks flat. */
  minSpan?: number;
  tickX?: (min: number, max: number, count: number) => number[];
}

export function LineChart({
  series,
  height: preferredHeight = 240,
  label,
  formatX,
  formatY,
  formatTipX,
  formatTipY,
  markers = [],
  polarity = false,
  dots = false,
  includeZero = false,
  minSpan = 0,
  tickX = niceTicks,
}: LineChartProps) {
  const [ref, width, height] = useSize(preferredHeight);
  const clipId = useId();
  const [hover, setHover] = useState<number | null>(null);

  const scales = useMemo(() => {
    const points = series.flatMap((s) => s.points).filter((p) => p.y !== null);
    if (!points.length || width === 0) return null;
    const xs = points.map((p) => p.x);
    const ys = points.map((p) => p.y as number);
    const xDomain: [number, number] = [Math.min(...xs), Math.max(...xs)];
    let yMin = Math.min(...ys);
    let yMax = Math.max(...ys);
    if (includeZero) {
      yMin = Math.min(yMin, 0);
      yMax = Math.max(yMax, 0);
    }
    if (yMax - yMin < minSpan) {
      const middle = (yMax + yMin) / 2;
      yMin = middle - minSpan / 2;
      yMax = middle + minSpan / 2;
    }
    const padding = (yMax - yMin || Math.abs(yMax) || 1) * 0.08;
    const yDomain: [number, number] = [yMin - padding, yMax + padding];
    const right = width - MARGIN.right;
    const bottom = height - MARGIN.bottom;
    return {
      x: linear(xDomain, [MARGIN.left, right]),
      y: linear(yDomain, [bottom, MARGIN.top]),
      xTicks: tickX(xDomain[0], xDomain[1], Math.max(2, Math.floor(width / 90))),
      yTicks: niceTicks(yDomain[0], yDomain[1], 4),
      right,
      bottom,
      xDomain,
    };
  }, [series, width, height, includeZero, minSpan, tickX]);

  const paths = useMemo(() => {
    if (!scales) return [];
    return series.map((s) => {
      let d = "";
      let pen = false;
      for (const point of s.points) {
        if (point.y === null) {
          pen = false;
          continue;
        }
        d += `${pen ? "L" : "M"}${scales.x(point.x).toFixed(1)} ${scales.y(point.y).toFixed(1)}`;
        pen = true;
      }
      return { name: s.name, color: s.color, d };
    });
  }, [series, scales]);

  const reference = series[0]?.points ?? [];
  const onMove = (event: React.PointerEvent<SVGRectElement>) => {
    if (!scales) return;
    const px = event.clientX - event.currentTarget.getBoundingClientRect().left + MARGIN.left;
    let best = 0;
    let bestDistance = Infinity;
    reference.forEach((point, index) => {
      const distance = Math.abs(scales.x(point.x) - px);
      if (distance < bestDistance) {
        best = index;
        bestDistance = distance;
      }
    });
    setHover(best);
  };

  const hovered = hover !== null ? reference[hover] : null;
  const zeroY = scales?.y(0);
  let lastLabelX = -Infinity;

  return (
    <div className="chart" ref={ref} style={chartStyle(preferredHeight)}>
      {scales && (
        <svg width={width} height={height} role="img" aria-label={label}>
          <defs>
            <clipPath id={`${clipId}-up`}>
              <rect x={0} y={0} width={width} height={Math.max(zeroY ?? 0, 0)} />
            </clipPath>
            <clipPath id={`${clipId}-down`}>
              <rect x={0} y={zeroY ?? 0} width={width} height={height} />
            </clipPath>
          </defs>
          {scales.yTicks.map((tick) => (
            <g key={tick}>
              <line
                x1={MARGIN.left}
                x2={scales.right}
                y1={scales.y(tick)}
                y2={scales.y(tick)}
                className={tick === 0 ? "axis-line" : "grid-line"}
              />
              <text x={MARGIN.left - 8} y={scales.y(tick) + 4} textAnchor="end" className="tick">
                {formatY(tick)}
              </text>
            </g>
          ))}
          {scales.xTicks.map((tick) => (
            <text key={tick} x={scales.x(tick)} y={height - 8} textAnchor="middle" className="tick">
              {formatX(tick)}
            </text>
          ))}
          {polarity &&
            zeroY !== undefined &&
            paths.map((path) => {
              const first = reference[0];
              const last = reference[reference.length - 1];
              const area = `${path.d}L${scales.x(last.x)} ${zeroY}L${scales.x(first.x)} ${zeroY}Z`;
              return (
                <g key={path.name}>
                  <path d={area} className="area-up" clipPath={`url(#${clipId}-up)`} />
                  <path d={area} className="area-down" clipPath={`url(#${clipId}-down)`} />
                </g>
              );
            })}
          {markers
            .filter((m) => m.x >= scales.xDomain[0] && m.x <= scales.xDomain[1])
            .sort((a, b) => a.x - b.x)
            .map((marker) => {
              const x = scales.x(marker.x);
              const showLabel = x - lastLabelX > 64;
              if (showLabel) lastLabelX = x;
              return (
                <g key={`${marker.label}-${marker.x}`}>
                  <line x1={x} x2={x} y1={MARGIN.top - 4} y2={scales.bottom} className="marker-line" />
                  {showLabel && (
                    <text x={x} y={MARGIN.top - 9} textAnchor="middle" className="marker-label">
                      {marker.label}
                    </text>
                  )}
                </g>
              );
            })}
          {paths.map((path) => (
            <path key={path.name} d={path.d} className="line" style={{ stroke: path.color }} />
          ))}
          {dots &&
            series.flatMap((s) =>
              s.points
                .filter((p) => p.y !== null)
                .map((p) => (
                  <circle
                    key={`${s.name}-${p.x}`}
                    cx={scales.x(p.x)}
                    cy={scales.y(p.y as number)}
                    r={5}
                    className="dot"
                    style={{ fill: s.color }}
                  />
                )),
            )}
          {hovered && (
            <g>
              <line
                x1={scales.x(hovered.x)}
                x2={scales.x(hovered.x)}
                y1={MARGIN.top}
                y2={scales.bottom}
                className="crosshair"
              />
              {series.map((s) => {
                const y = s.points[hover as number]?.y;
                return y === null || y === undefined ? null : (
                  <circle
                    key={s.name}
                    cx={scales.x(hovered.x)}
                    cy={scales.y(y)}
                    r={5}
                    className="dot"
                    style={{ fill: s.color }}
                  />
                );
              })}
            </g>
          )}
          <rect
            x={MARGIN.left}
            y={MARGIN.top}
            width={Math.max(scales.right - MARGIN.left, 0)}
            height={Math.max(scales.bottom - MARGIN.top, 0)}
            fill="transparent"
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
          />
        </svg>
      )}
      {scales && hovered && (
        <Tooltip x={scales.x(hovered.x)} width={width}>
          <strong>{(formatTipX ?? formatX)(hovered.x)}</strong>
          {series.map((s) => (
            <div key={s.name} className="tip-row">
              <span>
                <i style={{ background: s.color }} />
                {s.name}
              </span>
              <b>
                {s.points[hover as number]?.y == null
                  ? "–"
                  : (formatTipY ?? formatY)(s.points[hover as number].y as number)}
              </b>
            </div>
          ))}
        </Tooltip>
      )}
    </div>
  );
}

interface ColumnsProps {
  data: { x: number; a: number; b: number }[];
  names: [string, string];
  colors: [string, string];
  height?: number;
  label: string;
  formatX: (value: number) => string;
  formatY: (value: number) => string;
  marker?: { x: number; label: string };
}

function columnPath(x: number, width: number, top: number, bottom: number) {
  const radius = Math.min(4, width / 2, Math.max(bottom - top, 0));
  return (
    `M${x} ${bottom}V${top + radius}Q${x} ${top} ${x + radius} ${top}` +
    `H${x + width - radius}Q${x + width} ${top} ${x + width} ${top + radius}V${bottom}Z`
  );
}

export function PairedColumns({
  data,
  names,
  colors,
  height: preferredHeight = 240,
  label,
  formatX,
  formatY,
  marker,
}: ColumnsProps) {
  const [ref, width, height] = useSize(preferredHeight);
  const [hover, setHover] = useState<number | null>(null);
  const bottom = height - MARGIN.bottom;
  const plotWidth = Math.max(width - MARGIN.left - MARGIN.right, 0);
  const band = data.length ? plotWidth / data.length : 0;
  const barWidth = Math.max(Math.min(band / 2 - 1, 10), 1);
  const yMax = Math.max(...data.flatMap((d) => [d.a, d.b]), 1);
  const y = linear([0, yMax * 1.05], [bottom, MARGIN.top]);
  const center = (index: number) => MARGIN.left + band * (index + 0.5);
  const every = Math.max(1, Math.ceil(70 / (band || 1)));
  const hovered = hover !== null ? data[hover] : null;
  const markerIndex = marker ? data.findIndex((d) => d.x >= marker.x) : -1;

  return (
    <div className="chart" ref={ref} style={chartStyle(preferredHeight)}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={label}>
          {niceTicks(0, yMax * 1.05, 4).map((tick) => (
            <g key={tick}>
              <line
                x1={MARGIN.left}
                x2={width - MARGIN.right}
                y1={y(tick)}
                y2={y(tick)}
                className={tick === 0 ? "axis-line" : "grid-line"}
              />
              <text x={MARGIN.left - 8} y={y(tick) + 4} textAnchor="end" className="tick">
                {formatY(tick)}
              </text>
            </g>
          ))}
          {marker && markerIndex >= 0 && (
            <g>
              <line
                x1={center(markerIndex)}
                x2={center(markerIndex)}
                y1={MARGIN.top - 4}
                y2={bottom}
                className="marker-line"
              />
              <text
                x={center(markerIndex)}
                y={MARGIN.top - 9}
                textAnchor="middle"
                className="marker-label"
              >
                {marker.label}
              </text>
            </g>
          )}
          {data.map((d, index) => (
            <g key={d.x}>
              <path
                d={columnPath(center(index) - barWidth - 1, barWidth, y(d.a), bottom)}
                style={{ fill: colors[0] }}
              />
              <path
                d={columnPath(center(index) + 1, barWidth, y(d.b), bottom)}
                style={{ fill: colors[1] }}
              />
              {index % every === 0 && (
                <text x={center(index)} y={height - 8} textAnchor="middle" className="tick">
                  {formatX(d.x)}
                </text>
              )}
              <rect
                x={center(index) - band / 2}
                y={MARGIN.top}
                width={band}
                height={bottom - MARGIN.top}
                fill={hover === index ? "var(--hover-wash)" : "transparent"}
                onPointerEnter={() => setHover(index)}
                onPointerLeave={() => setHover(null)}
              />
            </g>
          ))}
        </svg>
      )}
      {hovered && hover !== null && (
        <Tooltip x={center(hover)} width={width}>
          <strong>{formatX(hovered.x)}</strong>
          <div className="tip-row">
            <span>
              <i style={{ background: colors[0] }} />
              {names[0]}
            </span>
            <b>{formatY(hovered.a)}</b>
          </div>
          <div className="tip-row">
            <span>
              <i style={{ background: colors[1] }} />
              {names[1]}
            </span>
            <b>{formatY(hovered.b)}</b>
          </div>
        </Tooltip>
      )}
    </div>
  );
}

function divergingBar(x: number, width: number, zero: number, value: number) {
  if (value <= zero) return columnPath(x, width, value, zero);
  const radius = Math.min(4, width / 2, value - zero);
  return (
    `M${x} ${zero}V${value - radius}Q${x} ${value} ${x + radius} ${value}` +
    `H${x + width - radius}Q${x + width} ${value} ${x + width} ${value - radius}V${zero}Z`
  );
}

interface DivergingProps {
  data: { key: string; a: number | null; b: number | null }[];
  names: [string, string];
  colors: [string, string];
  height?: number;
  label: string;
  formatX: (key: string) => string;
  formatY: (value: number) => string;
  formatTip: (value: number | null) => string;
}

/** Paired columns that grow up or down from a zero line. */
export function DivergingColumns({
  data,
  names,
  colors,
  height: preferredHeight = 260,
  label,
  formatX,
  formatY,
  formatTip,
}: DivergingProps) {
  const [ref, width, height] = useSize(preferredHeight);
  const [hover, setHover] = useState<number | null>(null);
  const margin = { top: 12, right: 8, bottom: 26, left: 58 };
  const bottom = height - margin.bottom;
  const plotWidth = Math.max(width - margin.left - margin.right, 0);
  const band = data.length ? plotWidth / data.length : 0;
  const barWidth = Math.max(Math.min(band / 2 - 2, 14), 1);
  const values = data.flatMap((d) => [d.a, d.b]).filter((v): v is number => v !== null);
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  const padding = (high - low || 1) * 0.06;
  const domain: [number, number] = [low - (low < 0 ? padding : 0), high + (high > 0 ? padding : 0)];
  const y = linear(domain, [bottom, margin.top]);
  const center = (index: number) => margin.left + band * (index + 0.5);
  const every = Math.max(1, Math.ceil(70 / (band || 1)));
  const hovered = hover !== null ? data[hover] : null;
  const zero = y(0);

  return (
    <div className="chart" ref={ref} style={chartStyle(preferredHeight)}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={label}>
          {niceTicks(domain[0], domain[1], 4).map((tick) => (
            <g key={tick}>
              <line
                x1={margin.left}
                x2={width - margin.right}
                y1={y(tick)}
                y2={y(tick)}
                className={tick === 0 ? "axis-line" : "grid-line"}
              />
              <text x={margin.left - 8} y={y(tick) + 4} textAnchor="end" className="tick">
                {formatY(tick)}
              </text>
            </g>
          ))}
          {data.map((d, index) => (
            <g key={d.key}>
              {d.a !== null && (
                <path
                  d={divergingBar(center(index) - barWidth - 1, barWidth, zero, y(d.a))}
                  style={{ fill: colors[0] }}
                />
              )}
              {d.b !== null && (
                <path
                  d={divergingBar(center(index) + 1, barWidth, zero, y(d.b))}
                  style={{ fill: colors[1] }}
                />
              )}
              {index % every === 0 && (
                <text x={center(index)} y={height - 8} textAnchor="middle" className="tick">
                  {formatX(d.key)}
                </text>
              )}
              <rect
                x={center(index) - band / 2}
                y={margin.top}
                width={band}
                height={Math.max(bottom - margin.top, 0)}
                fill={hover === index ? "var(--hover-wash)" : "transparent"}
                onPointerEnter={() => setHover(index)}
                onPointerLeave={() => setHover(null)}
              />
            </g>
          ))}
        </svg>
      )}
      {hovered && hover !== null && (
        <Tooltip x={center(hover)} width={width}>
          <strong>{formatX(hovered.key)}</strong>
          <div className="tip-row">
            <span>
              <i style={{ background: colors[0] }} />
              {names[0]}
            </span>
            <b>{formatTip(hovered.a)}</b>
          </div>
          <div className="tip-row">
            <span>
              <i style={{ background: colors[1] }} />
              {names[1]}
            </span>
            <b>{formatTip(hovered.b)}</b>
          </div>
        </Tooltip>
      )}
    </div>
  );
}

// A 2% day and a 30% year are both strong moves, so the colour scale widens with the period.
export const HEAT_SCALE: Record<string, number> = { "1D": 1, "1W": 2, "1M": 4, "3M": 7.5, "1Y": 15 };

const HEAT_THRESHOLDS = [-2, -1, -0.25, 0.25, 1, 2];

/** One of seven diverging classes: 0 is the deepest fall, 3 is flat, 6 is the strongest rise. */
export function heatClass(change: number): number {
  return HEAT_THRESHOLDS.filter((threshold) => change >= threshold).length;
}

interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Squarified treemap layout. Items must be sorted largest first. */
export function squarify<T extends { value: number }>(items: T[], box: Box): (T & Box)[] {
  const total = items.reduce((sum, item) => sum + item.value, 0);
  if (total <= 0 || box.width <= 0 || box.height <= 0) return [];
  const scale = (box.width * box.height) / total;
  const placed: (T & Box)[] = [];
  let remaining = { ...box };
  let index = 0;
  const worst = (row: number[], side: number) => {
    const sum = row.reduce((a, b) => a + b, 0);
    return Math.max(...row.map((area) => Math.max((side * side * area) / (sum * sum), (sum * sum) / (side * side * area))));
  };
  while (index < items.length) {
    const side = Math.min(remaining.width, remaining.height);
    const row: T[] = [items[index]];
    let areas = [items[index].value * scale];
    let next = index + 1;
    while (next < items.length) {
      const candidate = [...areas, items[next].value * scale];
      if (worst(candidate, side) > worst(areas, side)) break;
      areas = candidate;
      row.push(items[next]);
      next += 1;
    }
    const sum = areas.reduce((a, b) => a + b, 0);
    const horizontal = remaining.width >= remaining.height;
    const thickness = sum / side;
    let offset = 0;
    row.forEach((item, position) => {
      const length = areas[position] / thickness;
      placed.push({
        ...item,
        x: horizontal ? remaining.x : remaining.x + offset,
        y: horizontal ? remaining.y + offset : remaining.y,
        width: horizontal ? thickness : length,
        height: horizontal ? length : thickness,
      });
      offset += length;
    });
    remaining = horizontal
      ? { ...remaining, x: remaining.x + thickness, width: remaining.width - thickness }
      : { ...remaining, y: remaining.y + thickness, height: remaining.height - thickness };
    index = next;
  }
  return placed;
}

const GROUP_HEADER = 18;
const GAP = 2;

interface TreemapStock {
  symbol: string;
  name: string;
  change_pct: number;
  value: number;
}

export function Treemap({
  groups,
  height: preferredHeight,
  label,
}: {
  groups: { industry: string; value: number; stocks: TreemapStock[] }[];
  height: number;
  label: string;
}) {
  const [ref, width, height] = useSize(preferredHeight);
  const [hover, setHover] = useState<{ stock: TreemapStock; industry: string; x: number; y: number } | null>(null);
  const placed = useMemo(
    () => squarify([...groups].sort((a, b) => b.value - a.value), { x: 0, y: 0, width, height }),
    [groups, width, height],
  );
  return (
    <div className="chart treemap" ref={ref} style={chartStyle(preferredHeight)} onPointerLeave={() => setHover(null)}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={label}>
          {placed.map((group) => {
            const showHeader = group.width > 54 && group.height > GROUP_HEADER + 20;
            const headerSpace = showHeader ? GROUP_HEADER : 0;
            const tiles = squarify(
              [...group.stocks].sort((a, b) => b.value - a.value),
              {
                x: group.x + GAP,
                y: group.y + headerSpace + GAP,
                width: Math.max(group.width - GAP * 2, 0),
                height: Math.max(group.height - headerSpace - GAP * 2, 0),
              },
            );
            const maxChars = Math.floor((group.width - 8) / 6.2);
            return (
              <g key={group.industry}>
                <rect x={group.x} y={group.y} width={group.width} height={group.height} className="treemap-group" />
                {showHeader && (
                  <text x={group.x + 5} y={group.y + 13} className="treemap-header">
                    {group.industry.length > maxChars
                      ? `${group.industry.slice(0, Math.max(maxChars - 1, 1))}…`
                      : group.industry}
                  </text>
                )}
                {tiles.map((tile) => {
                  const heat = heatClass(tile.change_pct);
                  const symbolFits = tile.width - GAP > tile.symbol.length * 7 + 6 && tile.height - GAP > 16;
                  const changeFits = symbolFits && tile.height - GAP > 32;
                  const centerX = tile.x + (tile.width - GAP) / 2;
                  const centerY = tile.y + (tile.height - GAP) / 2;
                  return (
                    <g
                      key={tile.symbol}
                      onPointerMove={(event) => {
                        const bounds = (event.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
                        setHover({ stock: tile, industry: group.industry, x: event.clientX - bounds.left, y: event.clientY - bounds.top });
                      }}
                    >
                      <rect
                        x={tile.x}
                        y={tile.y}
                        width={Math.max(tile.width - GAP, 0.5)}
                        height={Math.max(tile.height - GAP, 0.5)}
                        rx={2}
                        className={`heat-fill-${heat}`}
                      />
                      {symbolFits && (
                        <text x={centerX} y={changeFits ? centerY - 2 : centerY + 4} textAnchor="middle" className={`treemap-label heat-text-${heat}`}>
                          {tile.symbol}
                        </text>
                      )}
                      {changeFits && (
                        <text x={centerX} y={centerY + 12} textAnchor="middle" className={`treemap-change heat-text-${heat}`}>
                          {`${tile.change_pct > 0 ? "+" : ""}${tile.change_pct.toFixed(2)}%`}
                        </text>
                      )}
                    </g>
                  );
                })}
              </g>
            );
          })}
        </svg>
      )}
      {hover && (
        <div
          className="chart-tooltip"
          style={{
            ...(hover.x > width * 0.62 ? { right: width - hover.x + 12 } : { left: hover.x + 12 }),
            top: Math.min(hover.y + 12, height - 96),
          }}
        >
          <strong>{hover.stock.symbol}</strong>
          <div className="secondary">{hover.stock.name}</div>
          <div className="tip-row">
            <span>Today</span>
            <b>{`${hover.stock.change_pct > 0 ? "+" : ""}${hover.stock.change_pct.toFixed(2)}%`}</b>
          </div>
          <div className="tip-row">
            <span>Value traded</span>
            <b>{(hover.stock.value / 1e7).toFixed(1)} Cr</b>
          </div>
          <div className="secondary">{hover.industry}</div>
        </div>
      )}
    </div>
  );
}

export interface CandlePoint {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  ema20: number | null;
  ema50: number | null;
}

export function Candles({
  data,
  height: preferredHeight = 340,
  label,
  formatDate,
  formatPrice,
}: {
  data: CandlePoint[];
  height?: number;
  label: string;
  formatDate: (iso: string) => string;
  formatPrice: (value: number) => string;
}) {
  const [ref, width, height] = useSize(preferredHeight);
  const [hover, setHover] = useState<number | null>(null);
  const margin = { top: 12, right: 14, bottom: 26, left: 62 };
  const plotWidth = Math.max(width - margin.left - margin.right, 0);
  const bottom = height - margin.bottom;
  const band = data.length ? plotWidth / data.length : 0;
  const center = (index: number) => margin.left + band * (index + 0.5);
  const values = data.flatMap((d) => [d.high, d.low, d.ema20, d.ema50]).filter((v): v is number => v !== null);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const padding = (high - low || high * 0.01 || 1) * 0.05;
  const domain: [number, number] = [low - padding, high + padding];
  const y = linear(domain, [bottom, margin.top]);
  const bodyWidth = Math.max(Math.min(band * 0.68, 11), 1);
  const every = Math.max(1, Math.ceil(84 / (band || 1)));

  const line = (key: "ema20" | "ema50") => {
    let d = "";
    let pen = false;
    data.forEach((point, index) => {
      const value = point[key];
      if (value === null) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${center(index).toFixed(1)} ${y(value).toFixed(1)}`;
      pen = true;
    });
    return d;
  };

  const onMove = (event: React.PointerEvent<SVGRectElement>) => {
    const px = event.clientX - event.currentTarget.getBoundingClientRect().left;
    setHover(Math.max(0, Math.min(data.length - 1, Math.floor(px / (band || 1)))));
  };
  const point = hover !== null ? data[hover] : null;
  const previous = hover !== null && hover > 0 ? data[hover - 1] : null;
  const change = point && previous ? (point.close / previous.close - 1) * 100 : null;

  return (
    <div className="chart" ref={ref} style={chartStyle(preferredHeight)}>
      {width > 0 && data.length > 0 && (
        <svg width={width} height={height} role="img" aria-label={label}>
          {niceTicks(domain[0], domain[1], 5).map((tick) => (
            <g key={tick}>
              <line x1={margin.left} x2={width - margin.right} y1={y(tick)} y2={y(tick)} className="grid-line" />
              <text x={margin.left - 8} y={y(tick) + 4} textAnchor="end" className="tick">
                {formatPrice(tick)}
              </text>
            </g>
          ))}
          {data.map((d, index) => {
            const up = d.close >= d.open;
            const top = y(Math.max(d.open, d.close));
            const bodyHeight = Math.max(Math.abs(y(d.open) - y(d.close)), 1);
            return (
              <g key={d.date} className={up ? "candle-up" : "candle-down"}>
                <line x1={center(index)} x2={center(index)} y1={y(d.high)} y2={y(d.low)} />
                <rect x={center(index) - bodyWidth / 2} y={top} width={bodyWidth} height={bodyHeight} rx={1} />
                {index % every === 0 && (
                  <text x={center(index)} y={height - 8} textAnchor="middle" className="tick">
                    {formatDate(d.date)}
                  </text>
                )}
              </g>
            );
          })}
          <path d={line("ema20")} className="line" style={{ stroke: "var(--overlay-1)" }} />
          <path d={line("ema50")} className="line" style={{ stroke: "var(--overlay-2)" }} />
          {hover !== null && (
            <line x1={center(hover)} x2={center(hover)} y1={margin.top} y2={bottom} className="crosshair" />
          )}
          <rect
            x={margin.left}
            y={margin.top}
            width={plotWidth}
            height={Math.max(bottom - margin.top, 0)}
            fill="transparent"
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
          />
        </svg>
      )}
      {point && hover !== null && (
        <Tooltip x={center(hover)} width={width}>
          <strong>{formatDate(point.date)}</strong>
          {(["open", "high", "low", "close"] as const).map((key) => (
            <div className="tip-row" key={key}>
              <span>{key[0].toUpperCase() + key.slice(1)}</span>
              <b>{formatPrice(point[key])}</b>
            </div>
          ))}
          {change !== null && (
            <div className="tip-row">
              <span>Change</span>
              <b>{`${change > 0 ? "+" : ""}${change.toFixed(2)}%`}</b>
            </div>
          )}
        </Tooltip>
      )}
    </div>
  );
}

export function Histogram({
  bars,
  height: preferredHeight = 240,
  label,
}: {
  bars: { label: string; count: number; tone: "up" | "down" }[];
  height?: number;
  label: string;
}) {
  const [ref, width, height] = useSize(preferredHeight);
  const margin = { top: 22, bottom: 40, side: 8 };
  const plot = Math.max(width - margin.side * 2, 0);
  const band = bars.length ? plot / bars.length : 0;
  const barWidth = Math.min(band - 6, 24);
  const bottom = height - margin.bottom;
  const peak = Math.max(...bars.map((bar) => bar.count), 1);
  const y = linear([0, peak], [bottom, margin.top]);
  return (
    <div className="chart" ref={ref} style={chartStyle(preferredHeight)}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={label}>
          <line x1={margin.side} x2={width - margin.side} y1={bottom} y2={bottom} className="axis-line" />
          {bars.map((bar, index) => {
            const x = margin.side + band * (index + 0.5);
            const [first, second] = bar.label.includes(" to ")
              ? [bar.label.split(" to ")[0], `to ${bar.label.split(" to ")[1]}`]
              : bar.label.split(" ");
            return (
              <g key={bar.label}>
                <title>{`${bar.label}: ${bar.count} stocks`}</title>
                {bar.count > 0 && (
                  <path
                    d={columnPath(x - barWidth / 2, barWidth, y(bar.count), bottom)}
                    className={bar.tone === "up" ? "fill-up" : "fill-down"}
                  />
                )}
                <text x={x} y={y(bar.count) - 6} textAnchor="middle" className="bar-value">
                  {bar.count}
                </text>
                <text x={x} y={bottom + 15} textAnchor="middle" className="tick">
                  {first}
                </text>
                <text x={x} y={bottom + 29} textAnchor="middle" className="tick">
                  {second}
                </text>
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}


interface ValueColumnsProps {
  data: { key: string; value: number; note?: string }[];
  height?: number;
  label: string;
  formatX: (key: string) => string;
  formatY: (value: number) => string;
  formatTip: (value: number) => string;
}

/** One column per period, up for gains and down for losses. */
export function ValueColumns({
  data,
  height: preferredHeight = 220,
  label,
  formatX,
  formatY,
  formatTip,
}: ValueColumnsProps) {
  const [ref, width, height] = useSize(preferredHeight);
  const [hover, setHover] = useState<number | null>(null);
  const margin = { top: 12, right: 8, bottom: 26, left: 64 };
  const bottom = height - margin.bottom;
  const plotWidth = Math.max(width - margin.left - margin.right, 0);
  const band = data.length ? plotWidth / data.length : 0;
  const barWidth = Math.max(Math.min(band - 2, 28), 1);
  const values = data.map((d) => d.value);
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  const padding = (high - low || 1) * 0.06;
  const domain: [number, number] = [low - (low < 0 ? padding : 0), high + (high > 0 ? padding : 0)];
  const y = linear(domain, [bottom, margin.top]);
  const zero = y(0);
  const every = Math.max(1, Math.ceil(70 / (band || 1)));
  const hovered = hover !== null ? data[hover] : null;
  return (
    <div className="chart" ref={ref} style={chartStyle(preferredHeight)}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={label}>
          {niceTicks(domain[0], domain[1], 4).map((tick) => (
            <g key={tick}>
              <line
                x1={margin.left}
                x2={width - margin.right}
                y1={y(tick)}
                y2={y(tick)}
                className={tick === 0 ? "axis-line" : "grid-line"}
              />
              <text x={margin.left - 8} y={y(tick) + 4} textAnchor="end" className="tick">
                {formatY(tick)}
              </text>
            </g>
          ))}
          {data.map((d, index) => {
            const x = margin.left + band * index + (band - barWidth) / 2;
            const top = Math.min(y(d.value), zero);
            const size = Math.max(Math.abs(y(d.value) - zero), d.value === 0 ? 0 : 1);
            return (
              <g key={d.key}>
                <rect
                  x={x}
                  y={top}
                  width={barWidth}
                  height={size}
                  rx={Math.min(3, barWidth / 3)}
                  className={d.value >= 0 ? "bar-gain" : "bar-loss"}
                  opacity={hover === null || hover === index ? 1 : 0.45}
                />
                {index % every === 0 && (
                  <text x={x + barWidth / 2} y={height - 8} textAnchor="middle" className="tick">
                    {formatX(d.key)}
                  </text>
                )}
              </g>
            );
          })}
          <rect
            x={margin.left}
            y={margin.top}
            width={plotWidth}
            height={Math.max(bottom - margin.top, 0)}
            fill="transparent"
            onPointerMove={(event) => {
              const px = event.clientX - event.currentTarget.getBoundingClientRect().left;
              const index = Math.floor(px / (band || 1));
              setHover(index >= 0 && index < data.length ? index : null);
            }}
            onPointerLeave={() => setHover(null)}
          />
        </svg>
      )}
      {hovered && hover !== null && (
        <Tooltip x={margin.left + band * (hover + 0.5)} width={width}>
          <strong>{formatX(hovered.key)}</strong>
          <div className="tip-row">
            <span>P&amp;L</span>
            <b>{formatTip(hovered.value)}</b>
          </div>
          {hovered.note && <div className="tip-row">{hovered.note}</div>}
        </Tooltip>
      )}
    </div>
  );
}

export interface ChartTrade {
  id: number;
  opened: string;
  closed: string;
  pnl: number;
  title: string;
  lines: string[];
}

interface TradeChartProps {
  bars: { day: string; close: number }[];
  trades: ChartTrade[];
  highlight?: number | null;
  onHighlight?: (id: number | null) => void;
  height?: number;
  label: string;
  formatY: (value: number) => string;
  formatDay: (day: string) => string;
  /** Shaded spans, such as the training and test windows. */
  spans?: { from: string; to: string; label: string }[];
}

/**
 * The underlying's daily close with every trade on it: a hollow dot where it opened, a solid
 * dot where it closed, joined by a line, green for a gain and red for a loss.
 */
export function TradeChart({
  bars,
  trades,
  highlight = null,
  onHighlight,
  height: preferredHeight = 360,
  label,
  formatY,
  formatDay,
  spans = [],
}: TradeChartProps) {
  const [ref, width, height] = useSize(preferredHeight);
  const [hoverDay, setHoverDay] = useState<number | null>(null);
  const index = useMemo(() => new Map(bars.map((bar, i) => [bar.day, i])), [bars]);
  // A trade on a day without a bar sits on the nearest earlier bar.
  const at = (day: string) => {
    if (index.has(day)) return index.get(day) as number;
    let found = -1;
    for (let i = 0; i < bars.length && bars[i].day <= day; i += 1) found = i;
    return Math.max(found, 0);
  };
  if (!bars.length) return <div className="chart" ref={ref} style={chartStyle(preferredHeight)} />;
  const closes = bars.map((bar) => bar.close);
  const low = Math.min(...closes);
  const high = Math.max(...closes);
  const padding = (high - low || 1) * 0.06;
  const right = width - MARGIN.right;
  const bottom = height - MARGIN.bottom;
  const x = linear([0, Math.max(bars.length - 1, 1)], [MARGIN.left, right]);
  const y = linear([low - padding, high + padding], [bottom, MARGIN.top]);
  const path = bars.map((bar, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(bar.close).toFixed(1)}`).join("");
  const tickEvery = Math.max(1, Math.ceil(bars.length / Math.max(2, Math.floor(width / 90))));
  const placed = trades.map((trade) => {
    const from = at(trade.opened);
    const to = at(trade.closed);
    return { trade, from, to };
  });
  const active =
    highlight !== null
      ? placed.find((p) => p.trade.id === highlight)
      : hoverDay !== null
        ? placed.find((p) => p.from <= hoverDay && hoverDay <= p.to)
        : undefined;
  return (
    <div className="chart" ref={ref} style={chartStyle(preferredHeight)}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={label}>
          {spans.map((span) => {
            const from = x(at(span.from));
            const to = x(at(span.to));
            return (
              <g key={span.label}>
                <rect x={from} y={MARGIN.top} width={Math.max(to - from, 0)} height={bottom - MARGIN.top} className="chart-span" />
                <text x={from + 6} y={MARGIN.top + 12} className="marker-label" textAnchor="start">
                  {span.label}
                </text>
              </g>
            );
          })}
          {niceTicks(low - padding, high + padding, 4).map((tick) => (
            <g key={tick}>
              <line x1={MARGIN.left} x2={right} y1={y(tick)} y2={y(tick)} className="grid-line" />
              <text x={MARGIN.left - 8} y={y(tick) + 4} textAnchor="end" className="tick">
                {formatY(tick)}
              </text>
            </g>
          ))}
          {bars.map((bar, i) =>
            i % tickEvery === 0 ? (
              <text key={bar.day} x={x(i)} y={height - 8} textAnchor="middle" className="tick">
                {formatDay(bar.day)}
              </text>
            ) : null,
          )}
          <path d={path} className="line" style={{ stroke: "var(--ink-3)" }} />
          {placed.map(({ trade, from, to }) => {
            const tone = trade.pnl >= 0 ? "trade-win" : "trade-loss";
            const dim = active && active.trade.id !== trade.id;
            return (
              <g key={trade.id} className={tone} opacity={dim ? 0.3 : 1}>
                <line x1={x(from)} y1={y(bars[from].close)} x2={x(to)} y2={y(bars[to].close)} className="trade-path" />
                <circle cx={x(from)} cy={y(bars[from].close)} r={4.5} className="trade-open" />
                <circle cx={x(to)} cy={y(bars[to].close)} r={4.5} className="trade-close" />
              </g>
            );
          })}
          {active && (
            <line
              x1={x(active.from)}
              x2={x(active.from)}
              y1={MARGIN.top}
              y2={bottom}
              className="crosshair"
            />
          )}
          <rect
            x={MARGIN.left}
            y={MARGIN.top}
            width={Math.max(right - MARGIN.left, 0)}
            height={Math.max(bottom - MARGIN.top, 0)}
            fill="transparent"
            onPointerMove={(event) => {
              const px = event.clientX - event.currentTarget.getBoundingClientRect().left;
              const day = Math.round((px / Math.max(right - MARGIN.left, 1)) * (bars.length - 1));
              setHoverDay(Math.min(Math.max(day, 0), bars.length - 1));
            }}
            onPointerLeave={() => {
              setHoverDay(null);
              onHighlight?.(null);
            }}
            onClick={() => active && onHighlight?.(active.trade.id)}
          />
        </svg>
      )}
      {width > 0 && (active || hoverDay !== null) && (
        <Tooltip x={x(active ? active.from : (hoverDay as number))} width={width}>
          {active ? (
            <>
              <strong>{active.trade.title}</strong>
              {active.trade.lines.map((text) => (
                <div key={text} className="tip-row">
                  {text}
                </div>
              ))}
            </>
          ) : (
            <>
              <strong>{formatDay(bars[hoverDay as number].day)}</strong>
              <div className="tip-row">
                <span>Close</span>
                <b>{formatY(bars[hoverDay as number].close)}</b>
              </div>
            </>
          )}
        </Tooltip>
      )}
    </div>
  );
}

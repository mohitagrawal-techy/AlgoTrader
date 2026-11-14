import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  LineStyle,
  type IChartApi,
  type Time,
} from "lightweight-charts";
import { api, type OptionCandles, type ReplayTrade } from "../api";
import { css, useThemeTick } from "./chartTheme";
import { legText, stopAt } from "./replay";

const HEIGHT = 220;
const BAR_SECONDS: Record<string, number> = { "5m": 300, "1d": 86400 };

/** The chart's wall-clock seconds as an ISO date. */
const isoDay = (seconds: number) =>
  new Date(seconds * 1000).toISOString().slice(0, 10);

/** A position-level P&L as one leg's option price, when the position has one leg. */
export function legPrice(trade: ReplayTrade, pnl: number | null | undefined) {
  if (trade.legs.length !== 1 || pnl === null || pnl === undefined) return null;
  const leg = trade.legs[0];
  return leg.entry_price + pnl / (leg.units * (leg.side === "buy" ? 1 : -1));
}

/** One traded contract's candles up to the replay's cursor, with the fill, the stop and the
 * target drawn on them. Candles after `until` are not shown, so nothing leaks ahead. */
export function TradeChart({
  trade,
  until,
}: {
  trade: ReplayTrade;
  until: number;
}) {
  const [legIndex, setLegIndex] = useState(0);
  const leg = trade.legs[legIndex] ?? trade.legs[0];
  const [candles, setCandles] = useState<OptionCandles | null>(null);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const themeTick = useThemeTick();

  const start = isoDay(trade.open_time);
  const end = isoDay(trade.close_time);
  useEffect(() => {
    let live = true;
    setCandles(null);
    setError(null);
    api
      .labReplayOption({
        symbol: trade.symbol,
        expiry: leg.expiry,
        strike: leg.strike,
        option_type: leg.option_type,
        start,
        end,
      })
      .then((found) => live && setCandles(found))
      .catch(
        (reason: Error) =>
          live && setError(reason.message || "The candles could not load."),
      );
    return () => {
      live = false;
    };
  }, [trade.symbol, leg.expiry, leg.strike, leg.option_type, start, end]);

  useEffect(() => {
    if (!box.current) return;
    const chart = createChart(box.current, {
      autoSize: true,
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderVisible: false,
      },
      rightPriceScale: { borderVisible: false },
      handleScroll: { mouseWheel: false, pressedMouseMove: true },
      handleScale: { mouseWheel: false, pinch: true },
    });
    chartRef.current = chart;
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !candles) return;
    const tick = css("--tick");
    const grid = css("--grid");
    chart.applyOptions({
      layout: {
        background: { color: "transparent" },
        textColor: tick,
        fontFamily: getComputedStyle(document.body).fontFamily,
        fontSize: 11,
      },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
    });
    const up = css("--good");
    const down = css("--critical");
    const series = chart.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      borderUpColor: up,
      borderDownColor: down,
      wickUpColor: up,
      wickDownColor: down,
      priceLineVisible: false,
    });
    const width = BAR_SECONDS[candles.interval] ?? 300;
    // A candle is shown once it has finished by the replay's cursor.
    const shown = candles.time.flatMap((time, i) =>
      time + width <= until || (candles.interval === "1d" && time < until)
        ? [
            {
              time: time as Time,
              open: candles.open[i],
              high: candles.high[i],
              low: candles.low[i],
              close: candles.close[i],
            },
          ]
        : [],
    );
    series.setData(shown);

    const single = trade.legs.length === 1;
    const stop = stopAt(trade, until);
    const lines: [string, number | null | undefined, string][] = [
      ["Entry", leg.entry_price, css("--accent")],
      [
        stop?.kind === "stop loss" || !stop ? "SL" : stop.kind,
        single ? legPrice(trade, stop?.level) : leg.stop_price,
        css("--pnl-down"),
      ],
      [
        "Target",
        single ? legPrice(trade, trade.target_pnl) : leg.target_price,
        css("--pnl-up"),
      ],
    ];
    // Keep the stop and target in view as well as the candles.
    const levels = lines
      .map(([, value]) => value)
      .filter((value): value is number => typeof value === "number");
    series.applyOptions({
      autoscaleInfoProvider: (
        original: () => {
          priceRange: { minValue: number; maxValue: number };
        } | null,
      ) => {
        const found = original();
        if (!found || !levels.length) return found;
        return {
          ...found,
          priceRange: {
            minValue: Math.min(found.priceRange.minValue, ...levels),
            maxValue: Math.max(found.priceRange.maxValue, ...levels),
          },
        };
      },
    });
    for (const [title, value, color] of lines) {
      if (value === null || value === undefined) continue;
      series.createPriceLine({
        price: value,
        color,
        lineWidth: 1,
        lineStyle: title === "Entry" ? LineStyle.Solid : LineStyle.Dashed,
        axisLabelVisible: true,
        title: `${title} ${value.toFixed(2)}`,
      });
    }

    // The fill uses the price known at the fill time: the close of the candle before it.
    const at = (moment: number) => {
      let found: number | null = null;
      for (const bar of shown) {
        if ((bar.time as number) + width <= moment) found = bar.time as number;
      }
      return found ?? (shown[0]?.time as number | undefined) ?? null;
    };
    const markers = [];
    const entryAt = at(trade.open_time);
    if (entryAt !== null) {
      markers.push({
        time: entryAt as Time,
        position: (leg.side === "buy" ? "belowBar" : "aboveBar") as "belowBar",
        color: css("--accent"),
        shape: (leg.side === "buy" ? "arrowUp" : "arrowDown") as "arrowUp",
        text: `${leg.side === "buy" ? "Buy" : "Sell"} ${leg.entry_price.toFixed(2)}`,
      });
    }
    if (trade.close_time < until) {
      const exitAt = at(trade.close_time);
      if (exitAt !== null && leg.exit_price !== null) {
        markers.push({
          time: exitAt as Time,
          position: "aboveBar" as const,
          color: trade.net_pnl >= 0 ? css("--pnl-up") : css("--pnl-down"),
          shape: "circle" as const,
          text: `Exit ${leg.exit_price.toFixed(2)}`,
        });
      }
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    createSeriesMarkers(series, markers);
    chart.timeScale().fitContent();
    return () => {
      // On unmount the chart itself is removed first, taking the series with it.
      if (chartRef.current === chart) chart.removeSeries(series);
    };
  }, [candles, until, trade, leg, themeTick]);

  return (
    <div className="trade-chart">
      {trade.legs.length > 1 && (
        <div className="trade-chart-legs" role="group" aria-label="Leg">
          {trade.legs.map((item, index) => (
            <button
              key={index}
              type="button"
              className={index === legIndex ? "is-on" : undefined}
              aria-pressed={index === legIndex}
              onClick={() => setLegIndex(index)}
            >
              {legText(item, trade.lots)}
            </button>
          ))}
        </div>
      )}
      <div className="trade-chart-head">
        <strong>{legText(leg, trade.lots)}</strong>
        <span className="muted">
          {candles?.interval === "1d" ? "daily" : "5-minute"} option candles
        </span>
      </div>
      <div className="trade-chart-canvas" style={{ height: HEIGHT }}>
        <div ref={box} className="trade-chart-box" />
        {error && <div className="trade-chart-note">{error}</div>}
        {!error && candles && candles.time.length === 0 && (
          <div className="trade-chart-note">
            No stored candles for this contract.
          </div>
        )}
        {!error && !candles && <div className="trade-chart-note">Loading…</div>}
      </div>
    </div>
  );
}

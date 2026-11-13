import type { Limit, SymbolExposure } from "../../api";
import { inr, inrCompact } from "../../format";
import { cx } from "../../components/ui";
import { DeskPanel } from "../DeskPanel";
import { byKey, FILL, levelOf, TEXT } from "./scale";

const HEADROOM = 1.25;

interface Greek {
  key: "delta" | "vega" | "gamma";
  label: string;
  unit: string;
  /** Signed net figure across the book. */
  net: number;
  /** The figure the limit is checked against. */
  used: number;
  long: string;
  short: string;
}

/** A bar that grows left for short and right for long, with the limit marked both sides. */
function GreekRow({ greek, limit }: { greek: Greek; limit: number }) {
  const domain = Math.max(limit * HEADROOM, greek.used, 1);
  const offset = (value: number) => Math.min(value / domain, 1) * 50;
  const half = (value: number) => `${offset(value)}%`;
  const level = levelOf(greek.used, limit);
  const side =
    greek.net === 0 ? "Flat" : greek.net > 0 ? greek.long : greek.short;
  const signed = greek.net < 0 ? -greek.used : greek.used;
  return (
    <li className="px-3 py-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[12.5px] text-ink">
          {greek.label}
          <span className="ml-2 text-[11.5px] text-ink-3">{greek.unit}</span>
        </span>
        <span className="font-mono text-[12px] tabular-nums">
          <span className={cx(level === "ok" ? "text-ink" : TEXT[level])}>
            {greek.used
              ? `${signed > 0 ? "+" : "−"}${inr(greek.used)}`
              : inr(0)}
          </span>
          <span className="text-ink-3"> / ±{inrCompact(limit)}</span>
        </span>
      </div>
      <div
        className="relative mt-2 h-1.5 bg-surface-2"
        role="meter"
        aria-label={`${greek.label}, ${side}`}
        aria-valuemin={0}
        aria-valuemax={limit}
        aria-valuenow={greek.used}
      >
        <span
          aria-hidden
          className={cx(
            "absolute inset-y-0 transition-[width] duration-150",
            FILL[level],
          )}
          style={
            greek.net < 0
              ? { right: "50%", width: half(greek.used) }
              : { left: "50%", width: half(greek.used) }
          }
        />
        <span
          aria-hidden
          className="absolute inset-y-0 left-1/2 w-px bg-axis"
        />
        {[-1, 1].map((sign) => (
          <span
            key={sign}
            aria-hidden
            className="absolute -inset-y-1 w-px bg-ink"
            style={{ left: `${50 + sign * offset(limit)}%` }}
          />
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-ink-3">
        <span>{greek.short}</span>
        <span className={cx(greek.net !== 0 && "text-ink-2")}>{side}</span>
        <span>{greek.long}</span>
      </div>
    </li>
  );
}

/** Net delta, vega and gamma of the open book against their limits. */
export function GreeksPanel({
  limits,
  bySymbol,
}: {
  limits: Limit[];
  bySymbol: SymbolExposure[];
}) {
  const sum = (pick: (row: SymbolExposure) => number) =>
    bySymbol.reduce((total, row) => total + pick(row), 0);
  const delta = sum((row) => row.delta_notional);
  const vega = sum((row) => row.vega);
  const gamma = sum((row) => row.gamma_1pct);
  const greeks: Greek[] = [
    {
      key: "delta",
      label: "Delta",
      unit: "notional",
      net: delta,
      used: byKey(limits, "delta")?.used ?? Math.abs(delta),
      long: "Long",
      short: "Short",
    },
    {
      key: "vega",
      label: "Vega",
      unit: "per vol point",
      net: vega,
      used: byKey(limits, "vega")?.used ?? Math.abs(vega),
      long: "Long vol",
      short: "Short vol",
    },
    {
      key: "gamma",
      label: "Gamma",
      unit: "P&L on a 1% move",
      net: gamma,
      used: byKey(limits, "gamma")?.used ?? Math.abs(gamma),
      long: "Long gamma",
      short: "Short gamma",
    },
  ];
  return (
    <DeskPanel title="Net Greeks" aside={<span>Limit marked both ways</span>}>
      <ul className="m-0 list-none divide-y divide-line pl-0">
        {greeks.map((greek) => {
          const limit = byKey(limits, greek.key)?.limit;
          return limit ? (
            <GreekRow key={greek.key} greek={greek} limit={limit} />
          ) : null;
        })}
      </ul>
    </DeskPanel>
  );
}

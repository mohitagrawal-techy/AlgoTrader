import type { Limit } from "../../api";
import { inr, num } from "../../format";
import { cx } from "../../components/ui";
import { DeskEmpty, DeskPanel } from "../DeskPanel";
import { byKey, CAPITAL_KEYS, FILL, levelOf, TEXT } from "./scale";

const HEADROOM = 1.25;
const pct = (value: number) => `${Math.min(Math.max(value, 0), 1) * 100}%`;

/** One limit as a bar against a fixed mark. The band before the mark is the last 30%. */
function LimitRow({ limit }: { limit: Limit }) {
  const format = limit.unit === "money" ? inr : (value: number) => num(value);
  const domain = Math.max(limit.limit * HEADROOM, limit.used, 1);
  const level = levelOf(limit.used, limit.limit);
  const share = limit.limit > 0 ? limit.used / limit.limit : 0;
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 gap-y-1.5 px-3 py-2.5">
      <span className="truncate text-[12.5px] text-ink">{limit.label}</span>
      <span className="font-mono text-[12px] tabular-nums text-ink-2">
        <span className="text-ink">{format(limit.used)}</span>
        <span className="text-ink-3"> / {format(limit.limit)}</span>
        <span className={cx("ml-2 inline-block w-11 text-right", TEXT[level])}>
          {Math.round(share * 100)}%
        </span>
      </span>
      <div
        className="relative col-span-2 h-1.5 bg-surface-2"
        role="meter"
        aria-label={limit.label}
        aria-valuemin={0}
        aria-valuemax={limit.limit}
        aria-valuenow={limit.used}
        aria-valuetext={`${format(limit.used)} of ${format(limit.limit)}`}
      >
        <span
          aria-hidden
          className="absolute inset-y-0 bg-warning-wash"
          style={{
            left: pct((limit.limit * 0.7) / domain),
            width: pct((limit.limit * 0.3) / domain),
          }}
        />
        <span
          aria-hidden
          className={cx(
            "absolute inset-y-0 left-0 transition-[width] duration-150",
            FILL[level],
          )}
          style={{ width: pct(limit.used / domain) }}
        />
        <span
          aria-hidden
          className="absolute -inset-y-1 w-px bg-ink"
          style={{ left: pct(limit.limit / domain) }}
        />
      </div>
    </li>
  );
}

/** Money and position limits from risk.yaml, each against its mark. */
export function LimitsPanel({ limits }: { limits: Limit[] }) {
  const rows = CAPITAL_KEYS.map((key) => byKey(limits, key)).filter(
    (limit): limit is Limit => !!limit,
  );
  const breached = rows.filter(
    (row) => levelOf(row.used, row.limit) === "breach",
  );
  return (
    <DeskPanel
      title="Limits"
      aside={
        breached.length ? (
          <span className="text-critical">
            {breached.length} at or past limit
          </span>
        ) : (
          <span>From config/risk.yaml</span>
        )
      }
    >
      {rows.length ? (
        <ul className="m-0 list-none divide-y divide-line pl-0">
          {rows.map((limit) => (
            <LimitRow key={limit.key} limit={limit} />
          ))}
        </ul>
      ) : (
        <DeskEmpty>No limits are configured.</DeskEmpty>
      )}
    </DeskPanel>
  );
}

import type { SymbolExposure } from "../../api";
import { inr, inrCompact, num, signedInr } from "../../format";
import { cx } from "../../components/ui";
import { DeskEmpty, DeskPanel } from "../DeskPanel";
import { FILL, levelOf, TEXT } from "./scale";

const signedCompact = (value: number) =>
  Math.abs(value) < 0.5 ? "0" : `${value > 0 ? "+" : ""}${inrCompact(value)}`;

const TH = "px-3 py-2 text-[11.5px] font-medium text-ink-3";
const TD = "px-3 py-2 font-mono text-[12px] tabular-nums";

/** Planned risk and Greeks for each underlying, against the per-underlying limit. */
export function ExposureTable({ rows }: { rows: SymbolExposure[] }) {
  return (
    <DeskPanel
      title="By underlying"
      aside={
        rows[0] && (
          <span>
            Risk limit per underlying{" "}
            <span className="font-mono tabular-nums">{inr(rows[0].limit)}</span>
          </span>
        )
      }
    >
      {rows.length ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] border-collapse">
            <thead>
              <tr className="border-b border-line text-right">
                <th scope="col" className={cx(TH, "text-left")}>
                  Underlying
                </th>
                <th scope="col" className={TH}>
                  Positions
                </th>
                <th scope="col" className={cx(TH, "w-[30%] text-left")}>
                  Planned risk
                </th>
                <th scope="col" className={TH}>
                  Delta
                </th>
                <th scope="col" className={TH}>
                  Vega
                </th>
                <th scope="col" className={TH}>
                  Gamma 1%
                </th>
                <th scope="col" className={TH}>
                  Open P&L
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const level = levelOf(row.planned_risk, row.limit);
                const share = row.limit > 0 ? row.planned_risk / row.limit : 0;
                return (
                  <tr
                    key={row.symbol}
                    className="border-b border-grid last:border-b-0"
                  >
                    <th
                      scope="row"
                      className="px-3 py-2 text-left text-[12.5px] font-medium text-ink"
                    >
                      {row.symbol}
                    </th>
                    <td className={cx(TD, "text-right text-ink-2")}>
                      {num(row.positions)}
                    </td>
                    <td className={TD}>
                      <div className="flex items-center gap-2">
                        <div
                          className="relative h-1.5 flex-1 bg-surface-2"
                          aria-hidden
                        >
                          <span
                            className={cx(
                              "absolute inset-y-0 left-0",
                              FILL[level],
                            )}
                            style={{ width: `${Math.min(share, 1) * 100}%` }}
                          />
                        </div>
                        <span
                          className={cx(
                            "w-24 text-right",
                            level === "ok" ? "text-ink" : TEXT[level],
                          )}
                        >
                          {inr(row.planned_risk)}
                        </span>
                      </div>
                    </td>
                    <td className={cx(TD, "text-right text-ink-2")}>
                      {signedCompact(row.delta_notional)}
                    </td>
                    <td className={cx(TD, "text-right text-ink-2")}>
                      {signedCompact(row.vega)}
                    </td>
                    <td className={cx(TD, "text-right text-ink-2")}>
                      {signedCompact(row.gamma_1pct)}
                    </td>
                    <td
                      className={cx(
                        TD,
                        "text-right",
                        row.open_pnl < 0
                          ? "text-down"
                          : row.open_pnl > 0
                            ? "text-up"
                            : "text-ink-3",
                      )}
                    >
                      {signedInr(row.open_pnl)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <DeskEmpty>No open positions, so nothing is at risk.</DeskEmpty>
      )}
    </DeskPanel>
  );
}

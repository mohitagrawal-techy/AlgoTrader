import type { StressGrid } from "../../api";
import { inr, inrCompact } from "../../format";
import { cx } from "../../components/ui";
import { DeskEmpty, DeskPanel } from "../DeskPanel";

const signedPct = (value: number) =>
  value === 0 ? "0" : `${value > 0 ? "+" : "−"}${Math.abs(value)}%`;

/** Tint a cell by how much of the stress limit its P&L amounts to. */
function tint(pnl: number, limit: number): string | undefined {
  if (Math.abs(pnl) < 1) return undefined;
  const share = Math.min(Math.abs(pnl) / Math.max(limit, 1), 1);
  const colour = pnl < 0 ? "var(--pnl-down)" : "var(--pnl-up)";
  return `color-mix(in srgb, ${colour} ${Math.round(6 + share * 44)}%, transparent)`;
}

export function worstCell(stress: StressGrid) {
  let worst = { pnl: 0, move: 0, shift: 0 };
  stress.pnl.forEach((row, i) =>
    row.forEach((pnl, j) => {
      if (pnl < worst.pnl)
        worst = {
          pnl,
          move: stress.spot_moves_pct[i],
          shift: stress.iv_shifts_pct[j],
        };
    }),
  );
  return worst;
}

/** Instant P&L of the open book across spot moves and IV shocks. */
export function StressPanel({
  stress,
  positions,
}: {
  stress: StressGrid;
  positions: number;
}) {
  const worst = worstCell(stress);
  const breaches = stress.pnl
    .flat()
    .filter((pnl) => pnl <= -stress.limit).length;
  return (
    <DeskPanel
      title="Stress"
      aside={
        <span>
          Limit{" "}
          <span className="font-mono tabular-nums">{inr(-stress.limit)}</span>
        </span>
      }
    >
      {positions === 0 ? (
        <DeskEmpty>The grid fills in once a paper position is open.</DeskEmpty>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse whitespace-nowrap font-mono text-[12px] tabular-nums [&_td]:px-2 [&_th]:px-2 sm:[&_td]:px-3 sm:[&_th]:px-3">
              <caption className="sr-only">
                Profit or loss if every underlying moved by the row's percentage
                and implied volatility changed by the column's percentage, with
                no time passing.
              </caption>
              <thead>
                <tr className="border-b border-line">
                  <th
                    scope="col"
                    className="px-3 py-2 text-left font-sans text-[11.5px] font-medium text-ink-3"
                  >
                    Spot \ IV
                  </th>
                  {stress.iv_shifts_pct.map((shift) => (
                    <th
                      key={shift}
                      scope="col"
                      className={cx(
                        "px-3 py-2 text-right text-[11.5px] font-medium",
                        shift === 0 ? "text-ink" : "text-ink-3",
                      )}
                    >
                      {signedPct(shift)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {stress.pnl.map((row, i) => {
                  const move = stress.spot_moves_pct[i];
                  return (
                    <tr
                      key={move}
                      className={cx(
                        "border-b border-grid last:border-b-0",
                        move === 0 && "border-y border-y-axis",
                      )}
                    >
                      <th
                        scope="row"
                        className={cx(
                          "px-3 py-1.5 text-left text-[11.5px] font-medium",
                          move === 0 ? "text-ink" : "text-ink-3",
                        )}
                      >
                        {signedPct(move)}
                      </th>
                      {row.map((pnl, j) => {
                        const shift = stress.iv_shifts_pct[j];
                        const isWorst =
                          worst.pnl < 0 &&
                          move === worst.move &&
                          shift === worst.shift;
                        const breach = pnl <= -stress.limit;
                        return (
                          <td
                            key={shift}
                            title={`Spot ${signedPct(move)}, IV ${signedPct(shift)}: ${inr(pnl)}`}
                            className={cx(
                              "px-3 py-1.5 text-right",
                              Math.abs(pnl) < 0.5 ? "text-ink-3" : "text-ink",
                              breach &&
                                "font-semibold text-critical outline outline-1 -outline-offset-1 outline-critical",
                              isWorst &&
                                !breach &&
                                "font-semibold outline outline-1 -outline-offset-1 outline-ink-2",
                            )}
                            style={{ background: tint(pnl, stress.limit) }}
                          >
                            {Math.abs(pnl) < 0.5
                              ? "0"
                              : `${pnl > 0 ? "+" : ""}${inrCompact(pnl)}`}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="m-0 border-t border-line px-3 py-2 text-[11.5px] leading-relaxed text-ink-3">
            {worst.pnl < 0 ? (
              <>
                Worst case{" "}
                <span className="font-mono text-ink-2 tabular-nums">
                  {inr(worst.pnl)}
                </span>{" "}
                at spot {signedPct(worst.move)} and IV {signedPct(worst.shift)}
                .{" "}
              </>
            ) : (
              "No cell in the grid loses money. "
            )}
            {breaches > 0 &&
              `${breaches} ${breaches === 1 ? "cell is" : "cells are"} past the stress limit. `}
            Every underlying moves together, with no time passing.
            {stress.approximated_legs > 0 &&
              ` ${stress.approximated_legs} ${stress.approximated_legs === 1 ? "leg has" : "legs have"} no usable price, so delta and gamma stand in for them.`}
          </p>
        </>
      )}
    </DeskPanel>
  );
}

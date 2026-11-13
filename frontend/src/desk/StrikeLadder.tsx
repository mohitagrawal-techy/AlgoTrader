import { useState } from "react";
import type { ChainRow, MarketPayload } from "../api";
import { compact, num, shortDate } from "../format";
import { Select, cx } from "../components/ui";
import { Bar, Price, TINT } from "./LadderCells";
import { DeskEmpty, DeskPanel, DeskSkeleton } from "./DeskPanel";

const AROUND = 8;

function nearest(rows: ChainRow[], spot: number): ChainRow[] {
  const at = rows.reduce(
    (best, row, index) =>
      Math.abs(row.strike - spot) < Math.abs(rows[best].strike - spot)
        ? index
        : best,
    0,
  );
  return rows.slice(Math.max(0, at - AROUND), at + AROUND + 1);
}

/** Strikes around the price for the nearest expiry: open interest as bars, bid and ask on
 * each side. A click opens that contract's chart. */
export function StrikeLadder({
  market,
}: {
  market: MarketPayload | null | undefined;
}) {
  const symbols = Object.keys(market?.symbols ?? {});
  const [symbol, setSymbol] = useState<string | null>(null);
  const current = market?.symbols[symbol ?? symbols[0]];
  const expiry = current?.expiries[0];
  const rows = current && expiry ? nearest(expiry.rows, current.spot) : [];
  const maxOi = Math.max(
    1,
    ...rows.flatMap((row) => [row.ce?.oi ?? 0, row.pe?.oi ?? 0]),
  );
  const atm = rows.reduce<number | null>(
    (best, row) =>
      best === null ||
      Math.abs(row.strike - (current?.spot ?? 0)) <
        Math.abs(best - (current?.spot ?? 0))
        ? row.strike
        : best,
    null,
  );
  const open = (strike: number, side: "CE" | "PE") => {
    if (!current || !expiry) return;
    window.location.hash = `market/chart/${current.symbol}/${expiry.expiry}/${strike}/${side}`;
  };

  return (
    <DeskPanel
      title="Strike ladder"
      aside={
        current && (
          <>
            <span className="font-mono">
              {expiry ? shortDate(expiry.expiry) : ""}
            </span>
            <Select
              small
              aria-label="Underlying"
              className="h-7 w-auto"
              value={current.symbol}
              onChange={(event) => setSymbol(event.target.value)}
            >
              {symbols.map((name) => (
                <option key={name}>{name}</option>
              ))}
            </Select>
          </>
        )
      }
    >
      {market === undefined ? (
        <DeskSkeleton rows={12} />
      ) : !current || !expiry || rows.length === 0 ? (
        <DeskEmpty>
          No option chain yet. It appears after the collector's first snapshot.
        </DeskEmpty>
      ) : (
        <table className="w-full border-collapse font-mono text-[12px] tabular-nums">
          <thead className="sticky top-0 bg-surface text-[11px] text-ink-3">
            <tr className="border-b border-line">
              <th className="w-[22%] px-2 py-1.5 text-right font-medium">OI</th>
              <th className="px-1 py-1.5 text-right font-medium">Bid</th>
              <th className="px-1 py-1.5 text-right font-medium">Ask</th>
              <th className="px-2 py-1.5 text-center font-medium text-ink-2">
                Strike
              </th>
              <th className="px-1 py-1.5 text-left font-medium">Bid</th>
              <th className="px-1 py-1.5 text-left font-medium">Ask</th>
              <th className="w-[22%] px-2 py-1.5 text-left font-medium">OI</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const itmCall = row.strike < current.spot;
              const isAtm = row.strike === atm;
              return (
                <tr
                  key={row.strike}
                  className={cx("border-b border-grid", isAtm && "bg-selected")}
                >
                  <td className={cx("px-2 py-1", itmCall && TINT.ce)}>
                    <Bar value={row.ce?.oi ?? null} max={maxOi} side="ce" />
                  </td>
                  <Price
                    value={row.ce?.bid}
                    align="right"
                    itm={itmCall}
                    side="ce"
                    dim
                    label={`Bid ${num(row.strike)} CE`}
                    onOpen={() => open(row.strike, "CE")}
                  />
                  <Price
                    value={row.ce?.ask}
                    align="right"
                    itm={itmCall}
                    side="ce"
                    label={`Ask ${num(row.strike)} CE`}
                    onOpen={() => open(row.strike, "CE")}
                  />
                  <td
                    className={cx(
                      "px-2 py-1 text-center font-medium",
                      isAtm ? "text-accent-ink" : "text-ink",
                    )}
                  >
                    {num(row.strike)}
                  </td>
                  <Price
                    value={row.pe?.bid}
                    align="left"
                    itm={!itmCall}
                    side="pe"
                    dim
                    label={`Bid ${num(row.strike)} PE`}
                    onOpen={() => open(row.strike, "PE")}
                  />
                  <Price
                    value={row.pe?.ask}
                    align="left"
                    itm={!itmCall}
                    side="pe"
                    label={`Ask ${num(row.strike)} PE`}
                    onOpen={() => open(row.strike, "PE")}
                  />
                  <td className={cx("px-2 py-1", !itmCall && TINT.pe)}>
                    <Bar value={row.pe?.oi ?? null} max={maxOi} side="pe" />
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <td
                colSpan={7}
                className="px-3 py-2 font-sans text-[11.5px] text-ink-3"
              >
                Spot{" "}
                <span className="font-mono text-ink-2">
                  {num(current.spot, 2)}
                </span>
                . Calls on the left, puts on the right; the longest bar is{" "}
                {compact(maxOi)} open interest. Choose a price to open its
                chart.
              </td>
            </tr>
          </tfoot>
        </table>
      )}
    </DeskPanel>
  );
}

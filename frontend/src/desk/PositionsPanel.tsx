import type { Portfolio, Position } from "../api";
import { inr, num, shortDate, signedInr } from "../format";
import { Flash, Pnl } from "../components/ui";
import { DeskEmpty, DeskPanel, DeskSkeleton } from "./DeskPanel";

/** "+24,500 CE 20 Nov": each leg's side (+ bought, − sold), strike and expiry. */
function legsText(position: Position): string {
  return position.legs
    .map(
      (leg) =>
        `${leg.side > 0 ? "+" : "−"}${num(leg.strike)} ${leg.option_type} ${shortDate(leg.expiry)}`,
    )
    .join(", ");
}

/** Open paper positions with their live mark-to-market. */
export function PositionsPanel({
  portfolio,
}: {
  portfolio: Portfolio | undefined;
}) {
  const live = portfolio?.live ?? [];
  return (
    <DeskPanel
      title="Open positions"
      aside={
        <a
          href="#paper"
          className="text-accent-ink no-underline hover:underline"
        >
          Paper trades
        </a>
      }
    >
      {portfolio === undefined ? (
        <DeskSkeleton />
      ) : live.length === 0 ? (
        <DeskEmpty>
          No open paper positions. Ideas that pass the filters open here.
        </DeskEmpty>
      ) : (
        <table className="w-full border-collapse text-[12.5px]">
          <thead className="sticky top-0 bg-surface text-[11px] text-ink-3">
            <tr className="border-b border-line text-left">
              <th className="px-3 py-1.5 font-medium">Position</th>
              <th className="px-2 py-1.5 font-medium">Legs</th>
              <th className="px-2 py-1.5 text-right font-medium">Lots</th>
              <th className="px-2 py-1.5 text-right font-medium">Stop</th>
              <th className="px-2 py-1.5 text-right font-medium">Target</th>
              <th className="px-3 py-1.5 text-right font-medium">P&amp;L</th>
            </tr>
          </thead>
          <tbody className="font-mono tabular-nums">
            {live.map((position) => (
              <tr
                key={position.id}
                className="border-b border-grid hover:bg-hover"
              >
                <td className="px-3 py-1.5 font-sans whitespace-nowrap">
                  <span className="text-ink">{position.symbol}</span>
                  <span className="ml-1.5 text-[11.5px] text-ink-3">
                    {position.strategy.replaceAll("_", " ")}
                  </span>
                </td>
                <td className="px-2 py-1.5 text-[11.5px] whitespace-nowrap text-ink-2">
                  {legsText(position)}
                </td>
                <td className="px-2 py-1.5 text-right">{position.lots}</td>
                <td className="px-2 py-1.5 text-right text-down">
                  {inr(position.stop_pnl)}
                </td>
                <td className="px-2 py-1.5 text-right text-up">
                  {inr(position.target_pnl)}
                </td>
                <td className="px-3 py-1.5 text-right whitespace-nowrap">
                  <Flash value={position.unrealized_pnl}>
                    <Pnl value={position.unrealized_pnl}>
                      {signedInr(position.unrealized_pnl)}
                    </Pnl>
                  </Flash>
                  {position.stale && (
                    <span
                      className="ml-1 text-[11px] text-warning"
                      title="Quote is stale"
                    >
                      !
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </DeskPanel>
  );
}

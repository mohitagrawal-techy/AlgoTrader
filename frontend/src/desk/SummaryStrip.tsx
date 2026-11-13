import type { ForwardOverview, Portfolio } from "../api";
import { inr, signedInr } from "../format";
import { Flash, Pnl } from "../components/ui";

function Cell({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0 bg-surface px-3 py-2">
      <div className="text-[11.5px] text-ink-2">{label}</div>
      <div className="mt-0.5 font-mono text-[17px] font-medium tracking-tight text-ink tabular-nums">
        {children}
      </div>
    </div>
  );
}

/** The desk's headline figures in one ruled strip. */
export function SummaryStrip({
  portfolio,
  forward,
}: {
  portfolio: Portfolio | undefined;
  forward: ForwardOverview | undefined;
}) {
  const active = forward?.runs.filter((run) => run.status === "active").length;
  const money = (value: number | undefined, signed = false) =>
    value === undefined ? (
      "–"
    ) : (
      <Flash value={value}>
        <Pnl value={signed ? value : null}>
          {signed ? signedInr(value) : inr(value)}
        </Pnl>
      </Flash>
    );
  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-3 xl:grid-cols-6">
      <Cell label="Paper capital">{money(portfolio?.capital)}</Cell>
      <Cell label="Today">{money(portfolio?.day_pnl, true)}</Cell>
      <Cell label="Open P&L">{money(portfolio?.unrealized, true)}</Cell>
      <Cell label="Realised">{money(portfolio?.realized, true)}</Cell>
      <Cell label="Open positions">
        {portfolio ? portfolio.live.length : "–"}
      </Cell>
      <Cell label="Strategies running">{active ?? "–"}</Cell>
    </div>
  );
}

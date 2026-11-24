import type { RegimeBreakdown, RegimeGroup } from "../../api";
import { num, signedInr } from "../../format";
import { Pnl, Status, Table } from "../ui";

const GROUP_LABELS: Record<RegimeGroup, string> = {
  trend: "Trend",
  volatility: "Volatility",
  move: "Breakout or reversal",
};

/** The regimes a strategy suits or should avoid, as status words. */
export function RegimeTags({
  regimes,
  labels,
}: {
  regimes: { suits: string[]; avoid: string[] } | null | undefined;
  labels?: Record<string, string>;
}) {
  if (!regimes || (!regimes.suits.length && !regimes.avoid.length))
    return <span className="secondary">Not proven yet</span>;
  const name = (key: string) => labels?.[key] ?? REGIME_NAMES[key] ?? key;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {regimes.suits.map((key) => (
        <Status key={key} tone="good">
          {name(key)}
        </Status>
      ))}
      {regimes.avoid.map((key) => (
        <Status key={key} tone="critical">
          Avoid {name(key).toLowerCase()}
        </Status>
      ))}
    </span>
  );
}

export const REGIME_NAMES: Record<string, string> = {
  strong_bull: "Strong bull",
  strong_bear: "Strong bear",
  sideways: "Sideways",
  mild_trend: "Mild trend",
  high_vol: "High volatility",
  low_vol: "Low volatility",
  normal_vol: "Normal volatility",
  breakout: "Breakout",
  reversal: "Reversal",
  no_signal: "No breakout or reversal",
};

/** Training and test trades by the regime of the session each opened in. */
export function RegimeTable({ regimes }: { regimes: RegimeBreakdown }) {
  const groups: RegimeGroup[] = ["trend", "volatility", "move"];
  const suits = new Set(regimes.suits);
  const avoid = new Set(regimes.avoid);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-2 px-3 pt-3 text-[13px]">
        <span className="text-ink-2">Suits</span>
        <RegimeTags regimes={regimes} />
      </div>
      <Table>
        <thead>
          <tr>
            <th>Regime</th>
            <th className="right">Sessions</th>
            <th className="right">Trades</th>
            <th className="right">Win rate</th>
            <th className="right">Per trade</th>
            <th className="right">Net P&amp;L</th>
          </tr>
        </thead>
        {groups.map((group) => (
          <tbody key={group}>
            <tr>
              <th
                colSpan={6}
                scope="colgroup"
                className="pt-3 text-left text-[11.5px] font-medium text-ink-3"
              >
                {GROUP_LABELS[group]}
              </th>
            </tr>
            {regimes.rows
              .filter((row) => row.group === group)
              .map((row) => (
                <tr key={row.key}>
                  <td>
                    {row.label}
                    {suits.has(row.key) && (
                      <span className="ml-2 text-[11.5px] text-up">suits</span>
                    )}
                    {avoid.has(row.key) && (
                      <span className="ml-2 text-[11.5px] text-down">
                        avoid
                      </span>
                    )}
                  </td>
                  <td className="right">{num(row.sessions)}</td>
                  <td className="right">{num(row.trades)}</td>
                  <td className="right">
                    {row.win_rate === null
                      ? "–"
                      : `${num(row.win_rate * 100)}%`}
                  </td>
                  <td className="right">
                    {row.expectancy === null ? (
                      "–"
                    ) : (
                      <Pnl value={row.expectancy}>
                        {signedInr(row.expectancy)}
                      </Pnl>
                    )}
                  </td>
                  <td className="right">
                    {row.trades ? (
                      <Pnl value={row.net_pnl}>{signedInr(row.net_pnl)}</Pnl>
                    ) : (
                      "–"
                    )}
                  </td>
                </tr>
              ))}
          </tbody>
        ))}
      </Table>
      <p className="panel-note">
        Each session is labelled from candles up to the previous close, so a
        label is what was known before the trade. A regime needs{" "}
        {regimes.min_trades} trades before the lab says the strategy suits or
        should avoid it.
        {regimes.unlabelled_trades > 0 &&
          ` ${regimes.unlabelled_trades} trades opened before enough history existed to label them.`}
      </p>
    </div>
  );
}

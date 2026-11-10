import { useMemo } from "react";
import type { Portfolio, Position } from "../api";
import { LineChart, timeTicks } from "../charts";
import {
  EXIT_LABELS,
  STRATEGY_LABELS,
  dateTimeIst,
  inr,
  inrCompact,
  num,
  shortDate,
  signedInr,
  timeIst,
} from "../format";
import { Empty, Panel, Pnl, Status, Tile, type Tone } from "../ui";

function legsText(position: Position): string {
  return position.legs
    .map((leg) => `${leg.side > 0 ? "+" : "−"}${num(leg.strike)} ${leg.option_type}`)
    .join("  ");
}

function statusFor(position: Position): { tone: Tone; label: string } {
  if (position.status === "pending") return { tone: "neutral", label: "Waiting to fill" };
  if (position.status === "exiting") return { tone: "warning", label: "Exiting" };
  if (position.stale) return { tone: "warning", label: "Stale quotes" };
  return { tone: "good", label: "Open" };
}

function LiveTable({ positions }: { positions: Position[] }) {
  if (!positions.length) return <Empty title="No live positions" />;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Position</th>
            <th>Legs</th>
            <th>Expiry</th>
            <th className="right">Lots</th>
            <th className="right">Open P&amp;L</th>
            <th className="right">Target</th>
            <th className="right">Stop</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((position) => {
            const status = statusFor(position);
            return (
              <tr key={position.id}>
                <td>
                  <div className="primary">{STRATEGY_LABELS[position.strategy]}</div>
                  <div className="secondary">
                    #{position.id} {position.symbol}
                  </div>
                </td>
                <td className="legs-cell">{legsText(position)}</td>
                <td>{shortDate(position.front_expiry)}</td>
                <td className="right">{position.lots}</td>
                <td className="right">
                  <Pnl value={position.unrealized_pnl}>{signedInr(position.unrealized_pnl)}</Pnl>
                </td>
                <td className="right">{signedInr(position.target_pnl)}</td>
                <td className="right">{signedInr(position.stop_pnl)}</td>
                <td>
                  <Status tone={status.tone}>{status.label}</Status>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ClosedTable({ positions }: { positions: Position[] }) {
  if (!positions.length) return <Empty title="No closed trades yet" />;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Position</th>
            <th>Closed</th>
            <th>Outcome</th>
            <th className="right">Costs</th>
            <th className="right">Net P&amp;L</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((position) => (
            <tr key={position.id}>
              <td>
                <div className="primary">{STRATEGY_LABELS[position.strategy]}</div>
                <div className="secondary">
                  #{position.id} {position.symbol} x{position.lots}
                </div>
              </td>
              <td>{position.closed_at ? dateTimeIst(position.closed_at) : "–"}</td>
              <td className="secondary">
                {EXIT_LABELS[position.exit_reason ?? ""] ?? position.exit_reason}
                {position.settlement_estimated && " (estimated)"}
              </td>
              <td className="right">{inr(position.entry_costs + position.exit_costs)}</td>
              <td className="right">
                {position.realized_pnl === null ? (
                  "–"
                ) : (
                  <Pnl value={position.realized_pnl}>{signedInr(position.realized_pnl)}</Pnl>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PaperView({ portfolio }: { portfolio: Portfolio }) {
  const total = portfolio.realized + portfolio.unrealized;
  const series = useMemo(
    () => [
      {
        name: "Total P&L",
        color: "var(--series-1)",
        points: portfolio.equity.map((point) => ({
          x: new Date(point.ts).getTime(),
          y: point.pnl,
        })),
      },
    ],
    [portfolio.equity],
  );
  const spansDays =
    portfolio.equity.length > 1 &&
    new Date(portfolio.equity[portfolio.equity.length - 1].ts).getTime() -
      new Date(portfolio.equity[0].ts).getTime() >
      86400000;
  const formatTime = (x: number) => {
    const iso = new Date(x).toISOString();
    return spansDays ? dateTimeIst(iso).split(",")[0] : timeIst(iso);
  };
  return (
    <>
      <div className="tiles tiles-4">
        <Tile
          label="Total P&L"
          value={<Pnl value={total}>{signedInr(total)}</Pnl>}
          hint={`On ${inr(portfolio.capital)} paper capital`}
        />
        <Tile label="Today" value={<Pnl value={portfolio.day_pnl}>{signedInr(portfolio.day_pnl)}</Pnl>} />
        <Tile
          label="Open P&L"
          value={<Pnl value={portfolio.unrealized}>{signedInr(portfolio.unrealized)}</Pnl>}
          hint="After entry costs"
        />
        <Tile
          label="Win rate"
          value={
            portfolio.closed_total
              ? `${Math.round((portfolio.wins / portfolio.closed_total) * 100)}%`
              : "–"
          }
          hint={`${portfolio.closed_total} closed ${portfolio.closed_total === 1 ? "trade" : "trades"}`}
        />
      </div>
      <Panel title="P&L over time" aside={<span className="muted">After costs, paper fills</span>}>
        {portfolio.equity.length < 2 ? (
          <Empty title="Not enough history to chart" />
        ) : (
          <LineChart
            series={series}
            polarity
            includeZero
            tickX={timeTicks}
            label="Total paper P&L over time"
            formatX={formatTime}
            formatY={inrCompact}
            formatTipX={(x) => dateTimeIst(new Date(x).toISOString())}
            formatTipY={(y) => signedInr(y, 2)}
          />
        )}
      </Panel>
      <Panel title="Live positions">
        <LiveTable positions={portfolio.live} />
      </Panel>
      <div className="grid-2 grid-wide-left">
        <Panel
          title="Closed trades"
          aside={
            portfolio.unfilled_total > 0 ? (
              <span className="muted">{portfolio.unfilled_total} ideas did not fill</span>
            ) : undefined
          }
        >
          <ClosedTable positions={portfolio.closed} />
        </Panel>
        <Panel title="Activity">
          {portfolio.activity.length === 0 ? (
            <Empty title="Nothing has happened yet" />
          ) : (
            <ol className="activity">
              {portfolio.activity.map((item, index) => (
                <li key={index}>
                  <time>{dateTimeIst(item.ts)}</time>
                  <span>
                    {item.position_id ? `#${item.position_id} ` : ""}
                    {item.kind.replaceAll("_", " ")}
                  </span>
                  <small>{item.detail.replaceAll("_", " ")}</small>
                </li>
              ))}
            </ol>
          )}
        </Panel>
      </div>
    </>
  );
}

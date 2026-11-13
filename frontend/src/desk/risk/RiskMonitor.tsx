import type { Portfolio } from "../../api";
import { inr, num, signedInr } from "../../format";
import { cx, Flash, Pnl } from "../../components/ui";
import { Cell, Strip } from "../SummaryStrip";
import { ExposureTable } from "./ExposureTable";
import { GreeksPanel } from "./GreeksPanel";
import { LimitsPanel } from "./LimitsPanel";
import { PnlSplit } from "./PnlSplit";
import { byKey, levelOf, TEXT } from "./scale";
import { StressPanel, worstCell } from "./StressPanel";

const ofLimit = (used: number, limit: number | undefined) =>
  limit ? `${Math.round((used / limit) * 100)}% of ${inr(limit)}` : undefined;

/** Where the paper book stands against every limit, and what a shock would do to it. */
export function RiskMonitor({ portfolio }: { portfolio: Portfolio }) {
  const { limits } = portfolio;
  const loss = byKey(limits, "daily_loss");
  const openRisk = byKey(limits, "open_risk");
  const margin = byKey(limits, "margin");
  const worst = worstCell(portfolio.stress);
  const stressLevel = levelOf(-worst.pnl, portfolio.stress.limit);
  return (
    <div className="flex flex-col gap-3">
      <Strip>
        <Cell label="Today" hint={loss && `Loss limit ${inr(loss.limit)}`}>
          <Flash value={portfolio.day_pnl}>
            <Pnl value={portfolio.day_pnl}>{signedInr(portfolio.day_pnl)}</Pnl>
          </Flash>
        </Cell>
        <Cell
          label="New entries"
          hint={
            portfolio.halted
              ? "Resume next session"
              : "Within the daily loss limit"
          }
        >
          <span
            className={cx(
              "font-sans",
              portfolio.halted ? "text-critical" : "text-up",
            )}
          >
            {portfolio.halted ? "Halted" : "Open"}
          </span>
        </Cell>
        <Cell
          label="Automatic paper trades"
          hint={
            portfolio.auto_trade
              ? `Ideas scoring ${num(portfolio.min_score)} or more`
              : "Ideas wait for you"
          }
        >
          <span
            className={cx(
              "font-sans",
              portfolio.auto_trade ? "text-ink" : "text-ink-3",
            )}
          >
            {portfolio.auto_trade ? "On" : "Off"}
          </span>
        </Cell>
        <Cell
          label="Open planned risk"
          hint={openRisk && ofLimit(openRisk.used, openRisk.limit)}
        >
          {inr(openRisk?.used ?? 0)}
        </Cell>
        <Cell
          label="Estimated margin"
          hint={margin && ofLimit(margin.used, margin.limit)}
        >
          {inr(margin?.used ?? 0)}
        </Cell>
        <Cell
          label="Worst stress"
          hint={`Limit ${inr(-portfolio.stress.limit)}`}
        >
          <span className={cx(stressLevel !== "ok" && TEXT[stressLevel])}>
            {worst.pnl < 0 ? inr(worst.pnl) : inr(0)}
          </span>
        </Cell>
      </Strip>
      <div className="grid gap-3 xl:grid-cols-12">
        <div className="flex min-w-0 flex-col xl:col-span-7">
          <LimitsPanel limits={limits} />
        </div>
        <div className="flex min-w-0 flex-col xl:col-span-5">
          <GreeksPanel limits={limits} bySymbol={portfolio.by_symbol} />
        </div>
        <div className="flex min-w-0 flex-col xl:col-span-7">
          <StressPanel
            stress={portfolio.stress}
            positions={portfolio.live.length}
          />
        </div>
        <div className="flex min-w-0 flex-col xl:col-span-5">
          <PnlSplit portfolio={portfolio} />
        </div>
        <div className="flex min-w-0 flex-col xl:col-span-12">
          <ExposureTable rows={portfolio.by_symbol} />
        </div>
      </div>
    </div>
  );
}

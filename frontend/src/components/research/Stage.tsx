import { Check, X } from "lucide-react";
import type {
  LabSummary,
  ResearchStage,
  ResearchStageInfo,
  ResearchStageKey,
} from "../../api";
import { inrCompact, num, shortDate, signedInr } from "../../format";
import { cx, Status, type Tone } from "../ui";

const TONES: Record<ResearchStageKey, Tone> = {
  reject: "critical",
  research: "warning",
  paper_trade: "good",
  approved: "good",
  live_monitoring: "good",
};

const LABELS: Record<ResearchStageKey, string> = {
  reject: "Reject",
  research: "Research",
  paper_trade: "Paper trade",
  approved: "Approved",
  live_monitoring: "Live monitoring",
};

/** A strategy's research stage as a status word, with the reason on hover. */
export function StageBadge({ stage }: { stage: ResearchStage | undefined }) {
  if (!stage?.stage) return <span className="secondary">Not tested</span>;
  return (
    <span title={stage.reason}>
      <Status tone={TONES[stage.stage]}>{LABELS[stage.stage]}</Status>
    </span>
  );
}

/** The five stages in order, with the strategy's own one marked. */
function Ladder({
  current,
  stages,
}: {
  current: ResearchStageKey | null;
  stages: ResearchStageInfo[];
}) {
  return (
    <ol className="m-0 hidden list-none grid-cols-5 sm:grid gap-px overflow-hidden rounded-md border border-line bg-line pl-0">
      {stages.map((info) => {
        const here = info.key === current;
        return (
          <li
            key={info.key}
            title={info.meaning}
            aria-current={here ? "step" : undefined}
            className={cx(
              "min-w-0 bg-surface px-2 py-1.5 text-center text-[11.5px]",
              here ? "bg-accent-wash font-semibold text-ink" : "text-ink-3",
            )}
          >
            <span className="block truncate">{info.label}</span>
          </li>
        );
      })}
    </ol>
  );
}

/** Net P&L of each consecutive period, as bars around a zero line. */
function Folds({ folds }: { folds: ResearchStage["folds"] }) {
  if (!folds.length) return null;
  const scale = Math.max(...folds.map((fold) => Math.abs(fold.net_pnl)), 1);
  return (
    <div>
      <p className="m-0 mb-1.5 text-[12px] text-ink-2">
        Net P&L in {folds.length} consecutive periods, training then unseen
      </p>
      <ol
        className="m-0 grid list-none gap-1 pl-0"
        style={{
          gridTemplateColumns: `repeat(${folds.length}, minmax(0, 1fr))`,
        }}
      >
        {folds.map((fold) => {
          const height = `${(Math.abs(fold.net_pnl) / scale) * 50}%`;
          const label = `${shortDate(fold.start)} to ${shortDate(fold.end)}: ${signedInr(fold.net_pnl)}, ${fold.trades} trades`;
          return (
            <li key={fold.start} className="min-w-0" title={label}>
              <div
                className="relative h-16 bg-surface-2"
                role="img"
                aria-label={label}
              >
                <span className="absolute inset-x-0 top-1/2 h-px bg-axis" />
                <span
                  className={cx(
                    "absolute inset-x-1",
                    fold.trades === 0
                      ? "bg-transparent"
                      : fold.net_pnl < 0
                        ? "bg-down"
                        : "bg-up",
                  )}
                  style={
                    fold.net_pnl < 0
                      ? { top: "50%", height }
                      : { bottom: "50%", height }
                  }
                />
              </div>
              <div className="mt-1 truncate text-center font-mono text-[11px] tabular-nums text-ink-2">
                {fold.trades ? inrCompact(fold.net_pnl) : "no trades"}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/** The stage, why, and each check the lab ran, passed or failed. */
export function StageCard({
  stage,
  stages,
}: {
  stage: ResearchStage;
  stages: ResearchStageInfo[];
}) {
  return (
    <div className="flex flex-col gap-3 p-3">
      <Ladder current={stage.stage} stages={stages} />
      <p className="m-0 text-[13px] text-ink">{stage.reason}</p>
      {stage.checks.length > 0 && (
        <ul className="m-0 grid list-none gap-x-6 gap-y-1.5 pl-0 sm:grid-cols-2">
          {stage.checks.map((check) => (
            <li
              key={check.key}
              className="flex min-w-0 items-start gap-2 text-[12.5px]"
            >
              {check.passed ? (
                <Check
                  aria-label="Passed"
                  size={14}
                  className="mt-0.5 shrink-0 text-up"
                />
              ) : (
                <X
                  aria-label="Failed"
                  size={14}
                  className="mt-0.5 shrink-0 text-down"
                />
              )}
              <span className="min-w-0">
                <span className="text-ink">{check.label}</span>
                <span className="block font-mono text-[11.5px] text-ink-3 tabular-nums">
                  {check.detail}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
      <Folds folds={stage.folds} />
    </div>
  );
}

const pct = (value: number | null | undefined, digits = 1) =>
  value === null || value === undefined ? "–" : `${num(value, digits)}%`;
const ratio = (value: number | null | undefined) =>
  value === null || value === undefined ? "–" : num(value, 2);

/** The full set of statistics for one window, in a ruled grid of label and figure. */
export function MetricsGrid({ summary }: { summary: LabSummary }) {
  const rows: [string, string][] = [
    ["Trades", num(summary.trades)],
    ["Net P&L", signedInr(summary.net_pnl)],
    ["Total return", pct(summary.total_return_pct)],
    [
      "Annual return",
      pct(
        (summary.annual_return ?? null) === null
          ? null
          : summary.annual_return! * 100,
      ),
    ],
    [
      "Win rate",
      pct(
        (summary.win_rate ?? null) === null ? null : summary.win_rate! * 100,
        0,
      ),
    ],
    ["Profit factor", ratio(summary.profit_factor)],
    ["Expectancy per trade", signedInr(summary.expectancy)],
    ["Average win", signedInr(summary.average_win)],
    ["Average loss", signedInr(summary.average_loss)],
    ["Reward to risk", ratio(summary.risk_reward)],
    ["Sharpe", ratio(summary.sharpe)],
    ["Sortino", ratio(summary.sortino)],
    [
      "Max drawdown",
      summary.max_drawdown == null
        ? "–"
        : `${signedInr(-summary.max_drawdown)} (${pct(summary.max_drawdown_pct)})`,
    ],
    ["Recovery factor", ratio(summary.recovery_factor)],
    [
      "Longest winning run",
      summary.longest_winning_streak == null
        ? "–"
        : `${summary.longest_winning_streak} trades`,
    ],
    [
      "Longest losing run",
      summary.longest_losing_streak == null
        ? "–"
        : `${summary.longest_losing_streak} trades`,
    ],
    ["Exposure", pct(summary.exposure_pct, 0)],
    ["Peak capital use", pct(summary.capital_use_pct, 0)],
  ];
  return (
    <dl className="m-0 grid grid-cols-1 gap-px bg-line sm:grid-cols-2 xl:grid-cols-3">
      {rows.map(([label, value]) => (
        <div
          key={label}
          className="flex items-baseline justify-between gap-3 bg-surface px-3 py-1.5"
        >
          <dt className="text-[12.5px] text-ink-2">{label}</dt>
          <dd className="m-0 font-mono text-[12.5px] text-ink tabular-nums">
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

import type { ForwardOverview, MarketPayload, Portfolio } from "../api";
import { DeskChart } from "./DeskChart";
import { EventLog } from "./EventLog";
import { PositionsPanel } from "./PositionsPanel";
import { StrategiesPanel } from "./StrategiesPanel";
import { StrikeLadder } from "./StrikeLadder";
import { SummaryStrip } from "./SummaryStrip";

/** The live paper desk: headline figures, the chart beside the strike ladder, what is
 * running and open, and the log of what just happened. Undefined data means still loading. */
export function Desk({
  portfolio,
  forward,
  market,
}: {
  portfolio: Portfolio | undefined;
  forward: ForwardOverview | undefined;
  market: MarketPayload | null | undefined;
}) {
  return (
    <div className="flex flex-col gap-3">
      <SummaryStrip portfolio={portfolio} forward={forward} />
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
        <div className="flex min-w-0 flex-col xl:col-span-8">
          <DeskChart />
        </div>
        <div className="flex min-w-0 flex-col xl:col-span-4 xl:max-h-[640px]">
          <StrikeLadder market={market} />
        </div>
        <div className="flex min-w-0 flex-col xl:col-span-5 xl:max-h-[320px]">
          <StrategiesPanel forward={forward} />
        </div>
        <div className="flex min-w-0 flex-col xl:col-span-7 xl:max-h-[320px]">
          <PositionsPanel portfolio={portfolio} />
        </div>
        <div className="flex min-w-0 flex-col max-h-[300px] xl:col-span-12">
          <EventLog portfolio={portfolio} />
        </div>
      </div>
    </div>
  );
}

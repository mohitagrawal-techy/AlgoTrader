import { ChartCandlestick, ChevronsRight, Map as MapIcon } from "lucide-react";
import type { ReadyBuilder } from "../hooks/useStrategyBuilder";
import { complexity, legText, sellsMoreThanBuys } from "../logic/spec";
import { Alert } from "../ui/Alert";
import { Button, ButtonLink } from "../ui/Button";
import { Hint } from "../ui/Field";
import { Badge } from "../ui/Section";
import { SummaryText } from "./SummaryText";

const LEVEL_TONE = {
  Simple: "neutral",
  Moderate: "call",
  Complex: "accent",
} as const;

/** The strategy at a glance beside the form, with saving. */
export function SummaryPanel({
  b,
  backHref,
  onChart,
  onMap,
  onHide,
}: {
  b: ReadyBuilder;
  backHref: string;
  onChart: () => void;
  onMap: () => void;
  onHide: () => void;
}) {
  const { spec, lab, problems, attempted, saveError, busy } = b;
  const level = complexity(spec);
  const count = Object.keys(problems).length;
  return (
    <aside
      id="strategy-summary"
      aria-label="Summary"
      className="flex flex-col rounded-lg border border-line bg-surface shadow-card xl:sticky xl:top-[68px]"
    >
      <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
        <h2 className="text-[15px] font-semibold [font-stretch:88%]">
          Summary
        </h2>
        <div className="flex items-center gap-1">
          <Badge tone={LEVEL_TONE[level]}>{level}</Badge>
          <Button
            variant="ghost"
            size="icon"
            icon={ChartCandlestick}
            aria-label="Preview chart"
            title="Preview chart"
            onClick={onChart}
          />
          <Button
            variant="ghost"
            size="icon"
            icon={MapIcon}
            aria-label="Preview strategy map"
            title="Preview strategy map"
            onClick={onMap}
          />
          <Button
            variant="ghost"
            size="icon"
            icon={ChevronsRight}
            aria-label="Hide the summary"
            title="Hide the summary"
            aria-expanded
            aria-controls="strategy-summary"
            onClick={onHide}
          />
        </div>
      </div>
      <ul className="border-b border-line">
        {spec.legs.map((leg, index) => (
          <li
            key={index}
            className="flex items-center gap-2 border-line px-4 py-2 text-[13px] [&+&]:border-t"
          >
            <Badge tone={leg.action === "buy" ? "call" : "put"}>
              {leg.action === "buy" ? "Buy" : "Sell"}
            </Badge>
            {legText(leg)}
          </li>
        ))}
      </ul>
      <div className="flex flex-col gap-3 p-4">
        <SummaryText spec={spec} catalog={lab.technical} />
        {sellsMoreThanBuys(spec) && (
          <Alert tone="warning">
            This sells more options than it buys, so its risk is undefined. The
            risk limits will usually block it.
          </Alert>
        )}
        {attempted && count > 0 && (
          <Alert tone="critical">
            Fix the{" "}
            {count === 1 ? "highlighted field" : `${count} highlighted fields`}{" "}
            to save.
          </Alert>
        )}
        {saveError && (
          <Alert tone="critical" title="Could not save">
            {saveError}
          </Alert>
        )}
        <Button
          variant="primary"
          size="md"
          disabled={busy}
          onClick={() => b.save(false)}
        >
          {busy ? "Saving…" : "Save strategy"}
        </Button>
        <Button
          size="md"
          disabled={busy || !lab.history.ready}
          onClick={() => b.save(true)}
          title={lab.history.ready ? undefined : "Testing needs more history"}
        >
          Save and test
        </Button>
        <ButtonLink href={backHref}>Cancel</ButtonLink>
        {!lab.history.ready && (
          <Hint>
            You can save now. Testing needs {lab.history.needed} sessions of
            history and {lab.history.sessions} are stored.
          </Hint>
        )}
      </div>
    </aside>
  );
}

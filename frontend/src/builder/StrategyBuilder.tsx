import { ChevronsLeft, Play } from "lucide-react";
import { useState } from "react";
import {
  BuilderErrorBoundary,
  BuilderProblem,
  BuilderSkeleton,
} from "./BuilderStates";
import { BuilderToolbar } from "./BuilderToolbar";
import { CasesSection } from "./cases/CasesSection";
import type { EditorRoute } from "./hooks/useLab";
import { useLayout } from "./hooks/useLayout";
import {
  useStrategyBuilder,
  type ReadyBuilder,
} from "./hooks/useStrategyBuilder";
import { ChartDialog, MapDialog } from "./PreviewDialogs";
import { BasicsSection, StartFromSection } from "./sections/BasicsSection";
import { BudgetSection } from "./sections/BudgetSection";
import { IndicatorsSection } from "./sections/IndicatorsSection";
import {
  ExpirySection,
  FeaturesSection,
  MarketFiltersSection,
  TargetsSection,
} from "./sections/PlanSections";
import { SummaryPanel } from "./summary/SummaryPanel";
import { Button, cx, FoldContext } from "../components/ui";

export type { EditorRoute };

/** Build, change or copy a strategy. */
export function StrategyEditor({ route }: { route: EditorRoute }) {
  return (
    <div className="builder-root">
      <BuilderErrorBoundary>
        <BuilderPage route={route} />
      </BuilderErrorBoundary>
    </div>
  );
}

function BuilderPage({ route }: { route: EditorRoute }) {
  const layout = useLayout();
  const b = useStrategyBuilder(route, layout.expandAll);
  const { state } = b;
  if (state.status === "loading") return <BuilderSkeleton />;
  if (state.status === "missing") {
    return (
      <BuilderProblem
        title="That strategy no longer exists"
        message="It may have been deleted from the lab."
      />
    );
  }
  if (state.status === "failed") {
    return (
      <BuilderProblem
        title="Could not load the strategy lab"
        message={state.message}
        onRetry={b.retry}
      />
    );
  }
  return (
    <BuilderForm b={{ ...b, lab: state.lab }} route={route} layout={layout} />
  );
}

function BuilderForm({
  b,
  route,
  layout,
}: {
  b: ReadyBuilder;
  route: EditorRoute;
  layout: ReturnType<typeof useLayout>;
}) {
  const [preview, setPreview] = useState<"chart" | "map" | null>(null);
  const { summaryOpen, setSummary } = layout;
  const backHref = route.mode === "edit" ? `#lab/view/${route.id}` : "#lab";
  return (
    <FoldContext.Provider value={layout.fold}>
      <div className="flex flex-col gap-3 text-[13.5px] text-ink">
        <BuilderToolbar
          title={route.mode === "edit" ? "Edit strategy" : "New strategy"}
          summaryOpen={summaryOpen}
          onCollapse={layout.collapseAll}
          onExpand={layout.expandAll}
          onSummary={() => setSummary(!summaryOpen)}
          onChart={() => setPreview("chart")}
          onMap={() => setPreview("map")}
        />
        <div
          className={cx(
            "grid items-start gap-4",
            summaryOpen && "xl:grid-cols-[minmax(0,1fr)_22rem]",
          )}
        >
          <div className="flex min-w-0 flex-col gap-3">
            {route.mode === "new" && <StartFromSection b={b} />}
            {/* One page, top to bottom: what it trades, what it reads and the signals made
                from that, the features switched on, the cases with their entry, exit and
                legs, the targets, expiry and timing, then market filters and sizing. */}
            <BasicsSection b={b} />
            <IndicatorsSection b={b} />
            <FeaturesSection b={b} />
            <CasesSection b={b} />
            <TargetsSection b={b} />
            <ExpirySection b={b} />
            <MarketFiltersSection b={b} />
            <BudgetSection b={b} />
            <RunBar b={b} />
          </div>
          {summaryOpen ? (
            <SummaryPanel
              b={b}
              backHref={backHref}
              onChart={() => setPreview("chart")}
              onMap={() => setPreview("map")}
              onHide={() => setSummary(false)}
            />
          ) : (
            <button
              type="button"
              aria-expanded={false}
              aria-controls="strategy-summary"
              title="Show the summary"
              onClick={() => setSummary(true)}
              className={cx(
                "fixed top-1/2 right-0 z-20 hidden -translate-y-1/2 cursor-pointer flex-col items-center gap-2",
                "rounded-l-md border border-r-0 border-line bg-surface px-1.5 py-3 text-[12px] font-semibold",
                "text-ink-2 shadow-card hover:text-ink xl:flex",
              )}
            >
              <ChevronsLeft aria-hidden className="size-4" />
              <span className="[writing-mode:vertical-rl]">Summary</span>
            </button>
          )}
        </div>
      </div>
      {preview === "chart" && (
        <ChartDialog b={b} onClose={() => setPreview(null)} />
      )}
      {preview === "map" && (
        <MapDialog b={b} onClose={() => setPreview(null)} />
      )}
    </FoldContext.Provider>
  );
}

/** Save, or save and run the backtest, at the foot of the form. */
function RunBar({ b }: { b: ReadyBuilder }) {
  const blocked = b.attempted && Object.keys(b.problems).length > 0;
  return (
    <div className="flex flex-wrap items-center justify-end gap-2 rounded-lg border border-line bg-surface px-4 py-3">
      {blocked && (
        <span className="mr-auto text-[12.5px] text-down">
          Fix the fields marked in red, then run again.
        </span>
      )}
      {b.saveError && (
        <span className="mr-auto text-[12.5px] text-down">{b.saveError}</span>
      )}
      <Button disabled={b.busy} onClick={() => b.save(false)}>
        Save
      </Button>
      <Button
        variant="primary"
        icon={Play}
        disabled={b.busy || !b.lab.history.ready}
        title={
          b.lab.history.ready
            ? "Save the strategy and run its backtest"
            : "Backtests need price history; load it on the Data page."
        }
        onClick={() => b.save(true)}
      >
        {b.busy ? "Saving…" : "Save and run backtest"}
      </Button>
    </div>
  );
}

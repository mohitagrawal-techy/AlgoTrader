import {
  ChartCandlestick,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  Map as MapIcon,
  PanelRight,
} from "lucide-react";
import { Button } from "./ui/Button";

/** Where the page sits, and controls for the whole form. On a phone the buttons scroll
 * sideways on one line instead of squeezing. */
export function BuilderToolbar({
  title,
  summaryOpen,
  onCollapse,
  onExpand,
  onSummary,
  onChart,
  onMap,
}: {
  title: string;
  summaryOpen: boolean;
  onCollapse: () => void;
  onExpand: () => void;
  onSummary: () => void;
  onChart: () => void;
  onMap: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
      <nav aria-label="Breadcrumb" className="w-full sm:w-auto">
        <ol className="flex items-center gap-1 text-[13px]">
          <li>
            <a
              href="#lab"
              className="text-ink-3 underline-offset-2 hover:text-ink hover:underline"
            >
              Strategy lab
            </a>
          </li>
          <li aria-hidden>
            <ChevronRight className="size-3.5 text-ink-3" />
          </li>
          <li aria-current="page" className="font-semibold text-ink">
            {title}
          </li>
        </ol>
      </nav>
      <div className="-mx-1 flex w-full gap-1.5 overflow-x-auto px-1 pb-1 [scrollbar-width:none] sm:w-auto sm:pb-0">
        <Button variant="ghost" icon={ChevronsDownUp} onClick={onCollapse}>
          Collapse all
        </Button>
        <Button variant="ghost" icon={ChevronsUpDown} onClick={onExpand}>
          Expand all
        </Button>
        <Button
          icon={PanelRight}
          aria-expanded={summaryOpen}
          aria-controls="strategy-summary"
          onClick={onSummary}
        >
          {summaryOpen ? "Hide summary" : "Show summary"}
        </Button>
        <Button icon={MapIcon} onClick={onMap}>
          Strategy map
        </Button>
        <Button variant="primary" icon={ChartCandlestick} onClick={onChart}>
          Preview chart
        </Button>
      </div>
    </div>
  );
}

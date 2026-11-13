import { IndexChart } from "../views/IndexChart";
import { StrategyVisual } from "../views/StrategyVisual";
import type { ReadyBuilder } from "./hooks/useStrategyBuilder";
import { MAX_RULES } from "./logic/picks";
import { Dialog } from "./ui/Dialog";

/** The strategy drawn as a map, updating as it is edited. */
export function MapDialog({
  b,
  onClose,
}: {
  b: ReadyBuilder;
  onClose: () => void;
}) {
  return (
    <Dialog
      size="lg"
      title={`${b.spec.name.trim() || "Strategy"} map`}
      description="Updates as you edit. Esc closes it."
      onClose={onClose}
    >
      <div className="strategy-map-dialog">
        <StrategyVisual
          spec={b.spec}
          catalog={b.lab.technical}
          signals={b.lab.signals}
        />
      </div>
    </Dialog>
  );
}

/** The strategy on the index chart: its indicators drawn, and arrows where the entry rules
 * held. A rule drawn on the chart is added to the open case. */
export function ChartDialog({
  b,
  onClose,
}: {
  b: ReadyBuilder;
  onClose: () => void;
}) {
  const { spec, lab, cases } = b;
  const picks = b.picks.picks;
  const description = picks.length
    ? `Its ${picks.length} indicator${picks.length === 1 ? "" : "s"} and levels are drawn, and arrows mark candles where each case's entry rules held. Esc closes it.`
    : "Pick indicators under Indicators and they are drawn here. Arrows mark candles where the entry rules held. Esc closes it.";
  return (
    <Dialog
      size="xl"
      title={`${spec.name.trim() || "Strategy"} on the chart`}
      description={description}
      onClose={onClose}
    >
      <IndexChart
        strategyPreview
        catalog={lab.technical}
        symbols={lab.chart_symbols ?? lab.symbols}
        tradable={lab.symbols}
        draft={{
          ...spec,
          name: spec.name.trim() || "Draft",
          indicators: picks,
        }}
        rules={spec.entry.rules ?? []}
        anyRules={spec.entry.any_rules ?? []}
        onAddRule={
          cases.rules.length < MAX_RULES
            ? (rule) => cases.setRules([...cases.rules, rule])
            : undefined
        }
      />
    </Dialog>
  );
}

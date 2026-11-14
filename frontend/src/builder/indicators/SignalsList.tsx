import { Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import type { BaseTechRule } from "../../api";
import type { ReadyBuilder } from "../hooks/useStrategyBuilder";
import { rulesText } from "../logic/ruleText";
import { SignalDialog } from "../rules/RulesEditor";
import { Button } from "../../components/ui";

/** The strategy's named signals: sets of conditions, such as "Sell signal 1", that each
 * case's entry and exit read as true or false. */
export function SignalsList({ b }: { b: ReadyBuilder }) {
  const catalog = b.lab.technical;
  const { signals, useCount, add, replace, remove } = b.signals;
  // The pop-up: closed, adding (null) or changing signal `index`.
  const [editing, setEditing] = useState<{ index: number | null } | null>(null);
  const current = editing?.index != null ? signals[editing.index] : undefined;

  return (
    <div className="flex flex-col gap-2">
      {signals.length > 0 && (
        <ul className="flex flex-col gap-2">
          {signals.map((signal, index) => {
            const uses = useCount(signal.name);
            return (
              <li
                key={index}
                id={`signal-${index}`}
                data-problem={
                  b.visible[`signal-${index}-name`] ? true : undefined
                }
                className="flex flex-col gap-2 rounded-md border border-line px-3 py-2.5 sm:flex-row sm:items-center"
              >
                <strong className="text-[13.5px] font-semibold text-ink sm:w-56 sm:shrink-0">
                  {signal.name}
                </strong>
                <span className="min-w-0 flex-1 text-[12.5px] text-ink-2">
                  {rulesText(catalog, signal.rules)}
                  <span className="ml-2 rounded-sm bg-surface-2 px-1.5 py-px text-[11.5px] text-ink-3">
                    {uses
                      ? `In ${uses} condition${uses === 1 ? "" : "s"}`
                      : "Not used yet"}
                  </span>
                </span>
                <div className="flex shrink-0 gap-1.5 self-end sm:self-auto">
                  <Button
                    icon={Pencil}
                    aria-label={`Edit ${signal.name}`}
                    onClick={() => setEditing({ index })}
                  >
                    Edit
                  </Button>
                  <Button
                    variant="danger"
                    icon={Trash2}
                    aria-label={`Delete ${signal.name}`}
                    onClick={() => {
                      if (
                        !uses ||
                        window.confirm(
                          `Delete "${signal.name}"? The ${uses} condition${uses === 1 ? "" : "s"} reading it go too.`,
                        )
                      )
                        remove(index);
                    }}
                  >
                    Delete
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <div>
        <Button
          icon={Plus}
          disabled={signals.length >= 12}
          onClick={() => setEditing({ index: null })}
        >
          Add signal
        </Button>
      </div>
      {editing && (
        <SignalDialog
          title={current ? `Edit ${current.name}` : "Add a signal"}
          catalog={catalog}
          picked={b.picks.picks}
          intraday={b.intraday}
          joins
          prefix={`signal-${editing.index ?? "new"}-draft`}
          initial={
            current
              ? structuredClone(current.rules)
              : [
                  // A first condition to change, on the candles the strategy decides on.
                  {
                    type: "compare",
                    timeframe:
                      (b.spec.decide_every ?? "day") === "day"
                        ? "1d"
                        : (b.spec.decide_every as "5m" | "15m" | "1h"),
                    left: { kind: "indicator", name: "close" },
                    op: "above",
                    right: { kind: "value", value: 0 },
                  },
                ]
          }
          hadRules={false}
          onAddPick={b.picks.addPick}
          named={{
            initial: current?.name ?? `Signal ${signals.length + 1}`,
            taken: signals
              .filter((_, i) => i !== editing.index)
              .map((signal) => signal.name),
          }}
          onSave={(rules, name) => {
            const signal = { name, rules: rules as BaseTechRule[] };
            if (editing.index === null) add(signal);
            else replace(editing.index, signal);
            setEditing(null);
          }}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

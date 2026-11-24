import { Plus } from "lucide-react";
import { useState } from "react";
import type { IndicatorPick, TechTimeframe } from "../../api";
import type { ReadyBuilder } from "../hooks/useStrategyBuilder";
import { DeleteIndicatorDialog } from "../indicators/DeleteIndicatorDialog";
import { IndicatorForm } from "../indicators/IndicatorForm";
import { IndicatorRow } from "../indicators/IndicatorRow";
import { SignalsList } from "../indicators/SignalsList";
import { byType, pickKey } from "../logic/picks";
import { Button, Hint, Badge, Section } from "../../components/ui";

/** The strategy's indicators grouped by type, and the form to add or change one. */
export function IndicatorsSection({ b }: { b: ReadyBuilder }) {
  const catalog = b.lab.technical;
  const { picks, useCount, addPick, replacePick, deletePick, rulesUsing } =
    b.picks;
  // The form: closed, adding (null) or changing one indicator.
  const [form, setForm] = useState<{ editing: IndicatorPick | null } | null>(
    null,
  );
  const [deleting, setDeleting] = useState<IndicatorPick | null>(null);
  const decide = b.spec.decide_every ?? "day";

  return (
    <Section
      title="Indicators"
      hint="What the rules can read. Pick each indicator on its own candle interval, the Current candle among them; every case's signals choose from these. Close, Open, High and Low need no picking."
      aside={<Badge>{picks.length}</Badge>}
    >
      {picks.length === 0 && (
        <Hint className="mb-3">
          No indicators yet. Rules can still compare prices with each other or
          with a number.
        </Hint>
      )}
      <div className="flex flex-col gap-4">
        {byType(
          catalog,
          picks.map((pick, index) => ({ pick, index })),
          (entry) => entry.pick.name,
        ).map((group) => (
          <div key={group.title}>
            <h3 className="mb-1.5 text-[12px] font-semibold text-ink-3">
              {group.title}
            </h3>
            <ul className="flex flex-col gap-2">
              {group.items.map(({ pick, index }) => {
                const uses = useCount[pickKey(catalog, pick)] ?? 0;
                return (
                  <IndicatorRow
                    key={index}
                    catalog={catalog}
                    pick={pick}
                    uses={uses}
                    onEdit={() => setForm({ editing: pick })}
                    onDelete={() =>
                      uses ? setDeleting(pick) : deletePick(pick)
                    }
                  />
                );
              })}
            </ul>
          </div>
        ))}
      </div>
      <Button
        variant="primary"
        icon={Plus}
        className="mt-4"
        onClick={() => setForm({ editing: null })}
      >
        Add indicator
      </Button>

      <div className="mt-6 border-t border-line pt-4">
        <h3 className="mb-1 text-[13px] font-semibold text-ink">Signals</h3>
        <p className="mb-3 text-[12.5px] text-ink-3">
          Named sets of conditions on these indicators, such as "Sell signal 1":
          the opening range closed green and the 5-minute low broke its open.
          Each case's Entry when and Exit when read them as true or false.
        </p>
        <SignalsList b={b} />
      </div>

      {form && (
        <IndicatorForm
          // A fresh form each time it opens, never the last one's half-filled state.
          key={form.editing ? pickKey(catalog, form.editing) : "new"}
          catalog={catalog}
          picks={picks}
          editing={form.editing}
          intraday={b.intraday}
          defaultFrame={decide === "day" ? "1d" : (decide as TechTimeframe)}
          onSave={(pick) => {
            if (form.editing) replacePick(form.editing, pick);
            else addPick(pick);
            setForm(null);
          }}
          onClose={() => setForm(null)}
        />
      )}
      {deleting && (
        <DeleteIndicatorDialog
          catalog={catalog}
          pick={deleting}
          uses={rulesUsing(deleting)}
          onConfirm={() => {
            deletePick(deleting);
            setDeleting(null);
          }}
          onClose={() => setDeleting(null)}
        />
      )}
    </Section>
  );
}

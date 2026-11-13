import type { ReadyBuilder } from "../hooks/useStrategyBuilder";
import { hhmm } from "../logic/spec";
import { AmountInput } from "../ui/amounts";
import { Field } from "../ui/Field";
import { Addon, InputGroup, NumberInput, TextInput } from "../ui/inputs";
import { Section } from "../ui/Section";

/** Exits every case shares: profit target, stop, time exits and daily limits. */
export function ExitWhenSection({ b }: { b: ReadyBuilder }) {
  const { spec, edit, visible, intraday } = b;
  const { exits, entry } = spec;
  return (
    <Section
      title="Exit when"
      hint="Shared by every case: the first of these to happen closes the whole trade."
    >
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <Field
          id="target"
          label="Take profit at"
          hint="0.5× premium is half of it; ₹ and points are per lot."
          problem={visible.target_frac}
        >
          <AmountInput
            id="target"
            value={exits.target_frac}
            unit={exits.target_unit}
            invalid={!!visible.target_frac}
            onChange={(value, unit) =>
              edit.exits({ target_frac: value as number, target_unit: unit })
            }
          />
        </Field>
        <Field
          id="stop"
          label="Cut loss at"
          hint="A tighter stop fits the per-trade limit."
          problem={visible.stop_frac}
        >
          <AmountInput
            id="stop"
            value={exits.stop_frac}
            unit={exits.stop_unit}
            invalid={!!visible.stop_frac}
            onChange={(value, unit) =>
              edit.exits({ stop_frac: value as number, stop_unit: unit })
            }
          />
        </Field>
        <Field
          id="exit-dte"
          label="Close before expiry"
          hint="At 15:00, this many days before."
          problem={visible.exit_dte}
        >
          <InputGroup>
            <NumberInput
              id="exit-dte"
              value={exits.exit_dte}
              invalid={!!visible.exit_dte}
              onChange={(value) => edit.exits({ exit_dte: value as number })}
            />
            <Addon>days</Addon>
          </InputGroup>
        </Field>
        {intraday && (
          <Field
            id="square-off"
            label="Square off by"
            problem={visible.square_off}
            hint="Closes every position by this time each day. Empty holds overnight."
          >
            <TextInput
              id="square-off"
              type="time"
              value={hhmm(exits.square_off)}
              invalid={!!visible.square_off}
              onChange={(event) =>
                edit.exits({ square_off: event.target.value || null })
              }
            />
          </Field>
        )}
      </div>
      <hr className="my-5 border-line" />
      <h3 className="mb-3 text-[13.5px] font-semibold">Daily limits</h3>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
        <Field
          id="daily-stop"
          label="Daily stop loss"
          hint="₹, all trades of this strategy that day."
          problem={visible.daily_stop}
        >
          <NumberInput
            id="daily-stop"
            value={exits.daily_stop ?? null}
            step={500}
            placeholder="Off"
            invalid={!!visible.daily_stop}
            onChange={(value) => edit.exits({ daily_stop: value })}
          />
        </Field>
        <Field
          id="daily-target"
          label="Daily take profit"
          hint="₹; then no more trades that day."
          problem={visible.daily_target}
        >
          <NumberInput
            id="daily-target"
            value={exits.daily_target ?? null}
            step={500}
            placeholder="Off"
            invalid={!!visible.daily_target}
            onChange={(value) => edit.exits({ daily_target: value })}
          />
        </Field>
        <Field
          id="max-trades"
          label="Max trades per day"
          hint="Empty means no limit."
          problem={visible.max_trades_per_day}
        >
          <NumberInput
            id="max-trades"
            value={entry.max_trades_per_day ?? null}
            placeholder="No limit"
            invalid={!!visible.max_trades_per_day}
            onChange={(value) => edit.entry({ max_trades_per_day: value })}
          />
        </Field>
      </div>
    </Section>
  );
}

import type { ReadyBuilder } from "../hooks/useStrategyBuilder";
import { hhmm } from "../logic/spec";
import { AmountInput } from "../amounts";
import {
  Field,
  Addon,
  InputGroup,
  NumberInput,
  TextInput,
  Section,
} from "../../components/ui";
import { Group } from "./Group";
import { StopFeatures } from "./TradeFeatures";

/** Exits every case shares: the profit target and stop, how the stop moves, time exits and
 * daily limits. */
export function ExitWhenSection({
  b,
  step,
}: {
  b: ReadyBuilder;
  step?: number;
}) {
  const { spec, edit, visible, intraday } = b;
  const { exits } = spec;
  return (
    <Section
      title="Exits and stops"
      step={step}
      hint="These apply to every trade. The first one reached closes it. A case's own exit signal can close it earlier."
    >
      <div className="flex flex-col gap-5">
        <Group
          title="Target and stop"
          hint="On the position's value. 0.5× premium is half the premium paid; ₹ and points are per lot."
        >
          <div className="mb-3 grid grid-cols-1 gap-4 md:grid-cols-3">
            <Field
              id="target"
              label="Take profit at"
              problem={visible.target_frac}
            >
              <AmountInput
                id="target"
                value={exits.target_frac}
                unit={exits.target_unit}
                invalid={!!visible.target_frac}
                onChange={(value, unit) =>
                  edit.exits({
                    target_frac: value as number,
                    target_unit: unit,
                  })
                }
              />
            </Field>
            <Field
              id="stop"
              label="Stop loss at"
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
          </div>
          <StopFeatures b={b} />
        </Group>

        <Group title="Time exits">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
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
                  onChange={(value) =>
                    edit.exits({ exit_dte: value as number })
                  }
                />
                <Addon>days</Addon>
              </InputGroup>
            </Field>
          </div>
        </Group>

        <Group
          title="Daily limits"
          hint="Across all of this strategy's trades that day. Reaching one closes open trades and stops new ones until tomorrow."
        >
          <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
            <Field
              id="daily-stop"
              label="Daily stop loss, ₹"
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
              label="Daily take profit, ₹"
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
          </div>
        </Group>
      </div>
    </Section>
  );
}

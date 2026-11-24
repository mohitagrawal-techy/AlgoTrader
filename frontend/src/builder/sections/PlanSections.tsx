import type { ReadyBuilder } from "../hooks/useStrategyBuilder";
import { hhmm, TRENDS } from "../logic/spec";
import { AmountInput } from "../amounts";
import {
  Addon,
  ChipCheck,
  Field,
  GroupLabel,
  Hint,
  InputGroup,
  NumberInput,
  Section,
  TextInput,
} from "../../components/ui";
import { ConditionsEditor } from "./ConditionsEditor";
import { Group } from "./Group";
import { EntryFeatures, StopFeatures } from "./TradeFeatures";

/** The features a trade can switch on: how its stop moves, and how it enters again. */
export function FeaturesSection({ b }: { b: ReadyBuilder }) {
  return (
    <Section
      title="Features"
      hint="Switch one on to set it up. They apply to every case."
    >
      <div className="flex flex-col gap-3">
        <StopFeatures b={b} />
        <EntryFeatures b={b} />
      </div>
    </Section>
  );
}

/** Profit target and stop for each trade, and limits for the whole day. */
export function TargetsSection({ b }: { b: ReadyBuilder }) {
  const { spec, edit, visible } = b;
  const { exits } = spec;
  return (
    <Section
      title="Targets"
      hint="On the position's value: 0.5× premium is half the premium paid; ₹ and points are per lot. A leg's own stop and target close only that leg."
    >
      <div className="grid gap-5 lg:grid-cols-2">
        <Group
          title="Transaction targets"
          hint="Each trade closes at the first reached."
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field id="stop" label="Stop loss" problem={visible.stop_frac}>
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
              id="target"
              label="Take profit"
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
          </div>
        </Group>
        <Group
          title="Daily targets"
          hint="Across the day's trades. Reaching one closes open trades and stops new ones until tomorrow."
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field
              id="daily-stop"
              label="Stop loss, ₹"
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
              label="Take profit, ₹"
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

/** Which expiry it trades, when in the day it may trade, and how often. */
export function ExpirySection({ b }: { b: ReadyBuilder }) {
  const { spec, edit, visible, intraday } = b;
  const { entry, exits } = spec;
  return (
    <Section
      title="Expiry and timing"
      hint={
        intraday
          ? "Intraday: decisions through the day, inside the trading window."
          : "Positional: one decision a day at the close. Choose a 5-minute to hourly decision under Strategy for intraday."
      }
    >
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {intraday && (
          <>
            <Field
              id="time-from"
              label="Trade from"
              hint="Empty means 09:15."
              problem={visible.time_from}
            >
              <TextInput
                id="time-from"
                type="time"
                value={hhmm(entry.time_from)}
                invalid={!!visible.time_from}
                onChange={(event) =>
                  edit.entry({ time_from: event.target.value || null })
                }
              />
            </Field>
            <Field
              id="time-to"
              label="Trade until"
              hint="Last time a trade may open."
              problem={visible.time_to}
            >
              <TextInput
                id="time-to"
                type="time"
                value={hhmm(entry.time_to)}
                invalid={!!visible.time_to}
                onChange={(event) =>
                  edit.entry({ time_to: event.target.value || null })
                }
              />
            </Field>
            <Field
              id="square-off"
              label="Square off by"
              hint="Empty holds overnight."
              problem={visible.square_off}
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
          </>
        )}
        <Field
          id="max-trades"
          label="Max transactions per day"
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
        <Field
          id="dte-min"
          label="Days to expiry, min"
          problem={visible.dte_min}
        >
          <NumberInput
            id="dte-min"
            value={entry.dte_min}
            invalid={!!visible.dte_min}
            onChange={(value) => edit.entry({ dte_min: value as number })}
          />
        </Field>
        <Field
          id="dte-max"
          label="Days to expiry, max"
          hint="The nearest expiry in this window is traded."
          problem={visible.dte_max}
        >
          <NumberInput
            id="dte-max"
            value={entry.dte_max}
            invalid={!!visible.dte_max}
            onChange={(value) => edit.entry({ dte_max: value as number })}
          />
        </Field>
        <Field
          id="exit-dte"
          label="Close before expiry"
          hint="At 15:00, this many days before. 0 is expiry day."
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
      </div>
    </Section>
  );
}

/** The market every entry needs: trend, implied volatility and readings such as PCR. */
export function MarketFiltersSection({ b }: { b: ReadyBuilder }) {
  const { spec, edit, visible } = b;
  const { entry } = spec;
  return (
    <Section
      title="Market filters"
      hint="Optional. On top of each case's entry signal; leave them empty to trade in any market."
    >
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <div className="col-span-2 flex flex-col gap-1.5">
          <GroupLabel id="trend-label">Trend</GroupLabel>
          <div
            className="flex flex-wrap gap-1.5"
            role="group"
            aria-labelledby="trend-label"
          >
            {TRENDS.map((trend) => (
              <ChipCheck
                key={trend}
                checked={entry.trend.includes(trend)}
                onChange={(on) =>
                  edit.entry({
                    trend: on
                      ? [...entry.trend, trend]
                      : entry.trend.filter((item) => item !== trend),
                  })
                }
              >
                <span className="capitalize">{trend}</span>
              </ChipCheck>
            ))}
          </div>
          <Hint>None selected means any trend.</Hint>
        </div>
        <Field id="iv-min" label="IV rank, min" problem={visible.iv_rank_min}>
          <NumberInput
            id="iv-min"
            value={entry.iv_rank_min}
            placeholder="Any"
            invalid={!!visible.iv_rank_min}
            onChange={(iv_rank_min) => edit.entry({ iv_rank_min })}
          />
        </Field>
        <Field id="iv-max" label="IV rank, max" problem={visible.iv_rank_max}>
          <NumberInput
            id="iv-max"
            value={entry.iv_rank_max}
            placeholder="Any"
            invalid={!!visible.iv_rank_max}
            onChange={(iv_rank_max) => edit.entry({ iv_rank_max })}
          />
        </Field>
      </div>
      <div className="mt-4">
        <ConditionsEditor b={b} />
      </div>
    </Section>
  );
}

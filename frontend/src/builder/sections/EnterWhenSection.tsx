import type { ReadyBuilder } from "../hooks/useStrategyBuilder";
import { hhmm, TRENDS } from "../logic/spec";
import {
  ChipCheck,
  Field,
  GroupLabel,
  Hint,
  NumberInput,
  TextInput,
  Section,
} from "../../components/ui";
import { ConditionsEditor } from "./ConditionsEditor";

/** Entry checks every case shares: trend, IV rank, days to expiry, entry hours, and market
 * conditions. */
export function EnterWhenSection({ b }: { b: ReadyBuilder }) {
  const { spec, edit, visible, intraday } = b;
  const { entry } = spec;
  return (
    <Section
      title="Enter when"
      hint="Shared by every case: each of these must hold as well as the case's own rules."
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
          problem={visible.dte_max}
        >
          <NumberInput
            id="dte-max"
            value={entry.dte_max}
            invalid={!!visible.dte_max}
            onChange={(value) => edit.entry({ dte_max: value as number })}
          />
        </Field>
        {intraday && (
          <>
            <Field
              id="time-from"
              label="Enter from"
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
              label="Enter until"
              hint="Empty means 15:25."
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
          </>
        )}
      </div>
      <hr className="my-5 border-line" />
      <ConditionsEditor b={b} />
    </Section>
  );
}

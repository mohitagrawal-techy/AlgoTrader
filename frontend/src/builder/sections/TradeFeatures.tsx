import type { LegUnit } from "../../api";
import { FEATURE_HELP } from "../../views/FeatureHelp";
import type { ReadyBuilder } from "../hooks/useStrategyBuilder";
import { UNIT_DEFAULTS } from "../logic/spec";
import { AmountInput } from "../amounts";
import {
  Field,
  InputGroup,
  NumberInput,
  Segmented,
  Select,
} from "../../components/ui";
import { FeatureCard } from "./FeatureCard";

const GRID = "grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3";

/** Ways the stop moves once a trade is in profit: trailing, and to cost. */
export function StopFeatures({ b }: { b: ReadyBuilder }) {
  const { spec, edit, visible } = b;
  const { exits } = spec;
  return (
    <div className={GRID}>
      <FeatureCard
        id="feature-trail"
        help={FEATURE_HELP.trail}
        label="Trailing stop"
        hint={
          exits.trail_step != null
            ? "For every step of gain, the stop moves up by the amount set."
            : "Once in profit, the stop follows the best P&L at this distance."
        }
        checked={exits.trail != null}
        onChange={(on) =>
          edit.exits({
            trail: on ? UNIT_DEFAULTS[exits.trail_unit ?? "premium"] / 2 : null,
            trail_step: null,
          })
        }
      >
        <Segmented
          label="How the stop trails"
          className="w-full"
          value={exits.trail_step != null ? "steps" : "distance"}
          options={[
            { value: "distance", label: "Behind the best price" },
            { value: "steps", label: "In steps (X / Y)" },
          ]}
          onChange={(how) =>
            edit.exits({
              trail_step:
                how === "steps"
                  ? (exits.trail ??
                      UNIT_DEFAULTS[exits.trail_unit ?? "premium"] / 2) * 5
                  : null,
            })
          }
        />
        {exits.trail_step != null && (
          <Field
            id="trail_step"
            label="For every gain of (X)"
            problem={visible.trail_step}
          >
            <AmountInput
              id="trail_step"
              value={exits.trail_step}
              unit={exits.trail_unit}
              invalid={!!visible.trail_step}
              onChange={(value, unit) =>
                edit.exits({ trail_step: value, trail_unit: unit })
              }
            />
          </Field>
        )}
        <Field
          id="trail"
          label={
            exits.trail_step != null ? "Move the stop up by (Y)" : "Distance"
          }
          problem={visible.trail}
        >
          <AmountInput
            id="trail"
            value={exits.trail}
            unit={exits.trail_unit}
            invalid={!!visible.trail}
            onChange={(value, unit) =>
              edit.exits({ trail: value, trail_unit: unit })
            }
          />
        </Field>
      </FeatureCard>
      <FeatureCard
        id="feature-breakeven"
        help={FEATURE_HELP.breakeven}
        label="Move stop to cost"
        hint="Once the gain reaches this, the stop moves to the entry price."
        checked={exits.breakeven_at != null}
        onChange={(on) =>
          edit.exits({
            breakeven_at: on
              ? UNIT_DEFAULTS[exits.breakeven_unit ?? "premium"] / 2
              : null,
          })
        }
      >
        <Field
          id="breakeven"
          label="After a gain of"
          problem={visible.breakeven_at}
        >
          <AmountInput
            id="breakeven"
            value={exits.breakeven_at}
            unit={exits.breakeven_unit}
            invalid={!!visible.breakeven_at}
            onChange={(value, unit) =>
              edit.exits({ breakeven_at: value, breakeven_unit: unit })
            }
          />
        </Field>
      </FeatureCard>
    </div>
  );
}

/** Whether and how it enters again: after a stop, after a target, and wait and trade. */
export function EntryFeatures({ b }: { b: ReadyBuilder }) {
  const { spec, edit, visible, intraday } = b;
  const { entry } = spec;
  const waiting = entry.wait_move != null;
  return (
    <div className={GRID}>
      <FeatureCard
        id="feature-reenter"
        help={FEATURE_HELP.reenter}
        label="Re-entry"
        hint="Enter again the same day after a stop loss closes a trade."
        checked={entry.reenter_after_stop ?? true}
        onChange={(on) => edit.entry({ reenter_after_stop: on })}
      />
      <FeatureCard
        id="feature-reexecute"
        help={FEATURE_HELP.reexecute}
        label="Re-execute"
        hint="Enter again the same day after a profit target closes a trade."
        checked={entry.reenter_after_target ?? true}
        onChange={(on) => edit.entry({ reenter_after_target: on })}
      />
      <FeatureCard
        id="feature-wait"
        help={FEATURE_HELP.wait}
        label="Wait and trade"
        disabled={!intraday}
        hint={
          intraday
            ? "When the entry rules hold, wait for the chosen options' premium to move before entering. A wait ends with the day."
            : "Needs a decision every 5, 15 or 60 minutes, set under Strategy."
        }
        checked={waiting}
        onChange={(on) =>
          edit.entry(
            on
              ? { wait_move: 10, wait_unit: "pct", wait_direction: "up" }
              : { wait_move: null },
          )
        }
      >
        <Field id="wait-move" label="Premium moves" problem={visible.wait_move}>
          <InputGroup>
            <Select
              aria-label="Direction"
              className="w-auto shrink-0"
              value={entry.wait_direction ?? "up"}
              onChange={(event) =>
                edit.entry({
                  wait_direction: event.target.value as "up" | "down",
                })
              }
            >
              <option value="up">Up</option>
              <option value="down">Down</option>
            </Select>
            <NumberInput
              id="wait-move"
              value={entry.wait_move ?? null}
              invalid={!!visible.wait_move}
              step={entry.wait_unit === "points" ? 1 : 5}
              onChange={(value) => edit.entry({ wait_move: value })}
            />
            <Select
              aria-label="Wait unit"
              className="w-auto shrink-0"
              value={entry.wait_unit ?? "pct"}
              onChange={(event) =>
                edit.entry({ wait_unit: event.target.value as LegUnit })
              }
            >
              <option value="pct">%</option>
              <option value="points">pts</option>
            </Select>
          </InputGroup>
        </Field>
      </FeatureCard>
    </div>
  );
}

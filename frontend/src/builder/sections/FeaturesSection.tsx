import type { LegUnit } from "../../api";
import { FEATURE_HELP } from "../../views/FeatureHelp";
import type { ReadyBuilder } from "../hooks/useStrategyBuilder";
import { UNIT_DEFAULTS } from "../logic/spec";
import { AmountInput } from "../amounts";
import {
  Field,
  InputGroup,
  NumberInput,
  Select,
  Badge,
  Section,
} from "../../components/ui";
import { FeatureCard } from "./FeatureCard";

/** Trailing stop, move stop to cost, re-entry, re-execute and wait and trade. */
export function FeaturesSection({ b }: { b: ReadyBuilder }) {
  const { spec, edit, visible, intraday } = b;
  const { exits, entry } = spec;
  const waiting = entry.wait_move != null;
  const count = [
    exits.trail != null,
    exits.breakeven_at != null,
    entry.reenter_after_stop ?? true,
    entry.reenter_after_target ?? true,
    intraday && waiting,
  ].filter(Boolean).length;

  return (
    <Section
      title="Features"
      hint="Optional ways to manage a trade. Each applies to every case."
      aside={<Badge>{count} on</Badge>}
    >
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        <FeatureCard
          id="feature-trail"
          help={FEATURE_HELP.trail}
          label="Trailing stop"
          hint="Once in profit, the stop follows the best P&L at this distance."
          checked={exits.trail != null}
          onChange={(on) =>
            edit.exits({
              trail: on
                ? UNIT_DEFAULTS[exits.trail_unit ?? "premium"] / 2
                : null,
            })
          }
        >
          <Field id="trail" label="Distance" problem={visible.trail}>
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
              : "Needs a decision every 5, 15 or 60 minutes, set under Basics."
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
          <Field
            id="wait-move"
            label="Premium moves"
            problem={visible.wait_move}
          >
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
    </Section>
  );
}

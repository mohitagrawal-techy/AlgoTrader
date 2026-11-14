import type { LabSpec } from "../../api";
import type { ReadyBuilder } from "../hooks/useStrategyBuilder";
import { BIASES, DECISIONS, MAX_RATIONALE } from "../logic/spec";
import {
  cx,
  ChipCheck,
  Segmented,
  Field,
  GroupLabel,
  Hint,
  CONTROL,
  Select,
  TextInput,
  Section,
} from "../../components/ui";

/** Begin blank, or from a copy of a standard strategy. Only offered for a new strategy. */
export function StartFromSection({ b }: { b: ReadyBuilder }) {
  const templates = b.lab.strategies.filter(
    (row) => row.spec.source === "template",
  );
  return (
    <Section
      title="Start from"
      hint="Begin blank, or copy the rules of a standard strategy."
    >
      <label className="sr-only" htmlFor="start-from">
        Start from
      </label>
      <Select
        id="start-from"
        defaultValue=""
        onChange={(event) => b.edit.startFrom(event.target.value)}
      >
        <option value="">Blank strategy</option>
        {templates.map((row) => (
          <option key={row.spec.id} value={row.spec.id}>
            {row.spec.name} ({row.legs_text})
          </option>
        ))}
      </Select>
    </Section>
  );
}

export function BasicsSection({ b, step }: { b: ReadyBuilder; step?: number }) {
  const { spec, edit, visible, intraday, lab } = b;
  return (
    <Section
      title="Basics"
      step={step}
      hint="What it trades, its market view, and how often it checks for a trade."
    >
      <div className="grid grid-cols-1 gap-4 md:grid-cols-12">
        <Field
          id="name"
          label="Name"
          problem={visible.name}
          className="md:col-span-7"
        >
          <TextInput
            id="name"
            maxLength={60}
            value={spec.name}
            invalid={!!visible.name}
            placeholder="For example: Bull call spread in uptrends"
            onChange={(event) => edit.set({ name: event.target.value })}
          />
        </Field>
        <div className="flex flex-col gap-1.5 md:col-span-5">
          <GroupLabel id="bias-label">Market view</GroupLabel>
          <Segmented
            name="bias"
            label="Market view"
            value={spec.bias}
            options={BIASES}
            onChange={(bias) => edit.set({ bias })}
          />
        </div>
        <div className="flex flex-col gap-1.5 md:col-span-12">
          <GroupLabel id="symbols-label">Trade on</GroupLabel>
          <div
            className="flex flex-wrap gap-1.5"
            role="group"
            aria-labelledby="symbols-label"
          >
            {lab.symbols.map((symbol) => (
              <ChipCheck
                key={symbol}
                checked={spec.symbols.includes(symbol)}
                onChange={(on) =>
                  edit.set({
                    symbols: on
                      ? [...spec.symbols, symbol]
                      : spec.symbols.filter((item) => item !== symbol),
                  })
                }
              >
                {symbol}
              </ChipCheck>
            ))}
          </div>
          <Hint>None ticked means every index.</Hint>
        </div>
        <Field
          id="decide"
          label="Decide when to trade"
          className="md:col-span-7"
          hint={
            intraday
              ? "Checked through the session; a trade enters at the next check. Uses minute data, NIFTY only for now."
              : "Rules are checked at the close and a trade enters at the next open."
          }
        >
          <Select
            id="decide"
            value={spec.decide_every ?? "day"}
            onChange={(event) =>
              edit.decision(event.target.value as LabSpec["decide_every"])
            }
          >
            {DECISIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          id="rationale"
          label="Why should this work?"
          className="md:col-span-12"
          hint={`${spec.rationale.length} of ${MAX_RATIONALE} characters. Optional.`}
        >
          <textarea
            id="rationale"
            rows={2}
            maxLength={MAX_RATIONALE}
            value={spec.rationale}
            aria-describedby="rationale-msg"
            className={cx(CONTROL, "h-auto py-2 leading-snug")}
            onChange={(event) => edit.set({ rationale: event.target.value })}
          />
        </Field>
      </div>
    </Section>
  );
}

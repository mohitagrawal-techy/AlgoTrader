import type { CandleType, TechCatalog, TechTimeframe } from "../../api";
import { pickStandardText, pickText } from "../logic/picks";
import { Alert } from "../ui/Alert";
import { Segmented } from "../ui/choices";
import { Field, GroupLabel, Hint } from "../ui/Field";
import { Select, TextInput } from "../ui/inputs";
import type { IndicatorFormState } from "./useIndicatorForm";

/** The chosen indicator's name, candles and settings, with whatever stops it saving. */
export function IndicatorSettings({
  catalog,
  form,
  intraday,
}: {
  catalog: TechCatalog;
  form: IndicatorFormState;
  intraday: boolean;
}) {
  const { chosen, pick } = form;
  if (!chosen) {
    return (
      <Hint className="py-6 text-center md:flex-1">
        Choose an indicator from the list to set it up.
      </Hint>
    );
  }
  const kinds = catalog.candle_types ?? [{ value: "normal", label: "Normal" }];
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <div>
        <h3 className="text-[15px] font-semibold">{chosen.label}</h3>
        {chosen.description && (
          <Hint className="mt-1">{chosen.description}</Hint>
        )}
        {chosen.formula && (
          <code className="mt-2 block rounded-sm bg-surface-2 px-2 py-1.5 font-mono text-[12px] leading-relaxed">
            {chosen.formula}
          </code>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-[minmax(0,1fr)_9rem_auto]">
        <Field
          id="indicator-label"
          label="Name"
          className="col-span-2 md:col-span-1"
          problem={
            form.nameTaken
              ? "Another indicator already has this name."
              : undefined
          }
          hint='Optional. Rules list it by this name, such as "fast EMA".'
        >
          <TextInput
            id="indicator-label"
            small
            maxLength={40}
            value={form.label}
            invalid={form.nameTaken}
            placeholder={
              pick
                ? pickStandardText(catalog, { ...pick, label: null })
                : undefined
            }
            onChange={(event) => form.setLabel(event.target.value)}
          />
        </Field>
        <Field id="indicator-interval" label="Candle interval">
          <Select
            id="indicator-interval"
            small
            value={form.timeframe}
            invalid={form.needsMinutes}
            onChange={(event) =>
              form.setTimeframe(event.target.value as TechTimeframe)
            }
          >
            {form.frames.map((frame) => (
              <option key={frame.value} value={frame.value}>
                {frame.label}
              </option>
            ))}
          </Select>
        </Field>
        <div className="flex flex-col gap-1.5">
          <GroupLabel id="indicator-chart-label">Chart type</GroupLabel>
          <Segmented
            name="indicator-chart"
            label="Chart type"
            value={form.candles}
            className="w-full"
            options={kinds.map((kind) => ({
              value: kind.value as CandleType,
              label: kind.value === "normal" ? "Candles" : kind.label,
            }))}
            onChange={form.setCandles}
          />
        </div>
      </div>
      {!intraday && (
        <Hint>
          5-minute to hourly intervals need a decision every 5, 15 or 60
          minutes, set under Basics.
        </Hint>
      )}
      {chosen.params.length ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          {chosen.params.map((param) => {
            const id = `indicator-param-${param.name}`;
            const problem = form.problemOf(param.name);
            return (
              <Field
                key={param.name}
                id={id}
                label={param.label}
                problem={problem}
                hint={`${param.min} to ${param.max}, default ${param.default}`}
              >
                <TextInput
                  id={id}
                  small
                  type="number"
                  inputMode="decimal"
                  invalid={!!problem}
                  step={param.whole ? 1 : "any"}
                  min={param.min}
                  max={param.max}
                  value={form.values[param.name] ?? ""}
                  onChange={(event) =>
                    form.setValue(param.name, event.target.value)
                  }
                />
              </Field>
            );
          })}
        </div>
      ) : (
        <Hint>No settings.</Hint>
      )}
      <div className="flex flex-col gap-1 text-[12.5px]">
        {chosen.fields.length > 0 && (
          <p>
            <span className="text-ink-3">Lines rules can read: </span>
            {chosen.fields.map((f) => f.label).join(", ")}
          </p>
        )}
        {pick && form.valid && (
          <p>
            <span className="text-ink-3">Listed as </span>
            <strong>{pickText(catalog, pick)}</strong>
          </p>
        )}
      </div>
      {form.duplicate && (
        <Alert tone="warning">
          The strategy already has {pick ? pickText(catalog, pick) : "this"}.
          Change a setting to add a second copy.
        </Alert>
      )}
      {form.needsMinutes && (
        <Alert tone="warning">
          {chosen.label} works on 5-minute, 15-minute or hourly candles. Choose
          one of those intervals.
        </Alert>
      )}
      {form.unavailable && (
        <Alert tone="warning">
          {chosen.label} needs 5-minute to hourly candles. Under Basics, decide
          every 5, 15 or 60 minutes first.
        </Alert>
      )}
    </div>
  );
}

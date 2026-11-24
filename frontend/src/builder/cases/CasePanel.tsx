import { Trash2 } from "lucide-react";
import type { ReadyBuilder } from "../hooks/useStrategyBuilder";
import { ruleText } from "../logic/ruleText";
import { BIASES, MAX_LEGS } from "../logic/spec";
import { RulesEditor } from "../rules/RulesEditor";
import {
  Button,
  Segmented,
  Switch,
  Field,
  GroupLabel,
  Hint,
  TextInput,
  Badge,
  Block,
} from "../../components/ui";
import { LegsEditor } from "./LegsEditor";

/** The open case: its name and view (after Case 1), entry signal, exit signal and legs. */
export function CasePanel({ b }: { b: ReadyBuilder }) {
  const { cases: c, visible, intraday, lab } = b;
  const catalog = lab.technical;
  const own = c.active === 0;
  const index = c.active - 1;
  const prefix = own ? "" : `case-${index}-`;
  const picked = b.picks.picks;
  const shared = c.item?.exit_rules == null && !own;
  const caseOneExits = b.spec.exits.rules ?? [];
  const signalNames = b.signals.signals.map((signal) => signal.name);

  return (
    <div
      role="tabpanel"
      id="case-panel"
      aria-labelledby={`case-tab-${c.active}`}
      className="flex flex-col gap-3 rounded-b-md border border-t-0 border-line bg-page/40 p-3 sm:p-4"
    >
      {own || !c.item ? (
        <Hint>
          Case 1 is the strategy's main setup. Its market view is set under
          Strategy. Add a case for another way into the trade, such as the put
          side of a breakout.
        </Hint>
      ) : (
        <div className="grid grid-cols-1 items-end gap-3 md:grid-cols-[1fr_auto_auto]">
          <Field
            id={`case-${index}-name`}
            label="Case name"
            problem={visible[`case-${index}-name`]}
          >
            <TextInput
              id={`case-${index}-name`}
              small
              maxLength={40}
              value={c.item.name}
              invalid={!!visible[`case-${index}-name`]}
              onChange={(event) =>
                c.setCase(index, { name: event.target.value })
              }
            />
          </Field>
          <div className="flex flex-col gap-1.5">
            <GroupLabel id={`case-${index}-bias-label`}>Market view</GroupLabel>
            <Segmented
              name={`case-${index}-bias`}
              label="Case market view"
              value={c.item.bias}
              options={BIASES}
              onChange={(bias) => c.setCase(index, { bias })}
            />
          </div>
          <Button
            variant="danger"
            icon={Trash2}
            onClick={() => c.removeCase(index)}
          >
            Remove case
          </Button>
        </div>
      )}

      {/* Entry and exit, a row each, then the legs it trades. */}
      <div className="flex flex-col gap-2">
        <RulesEditor
          key={`entry-${c.active}`}
          joins
          catalog={catalog}
          picked={picked}
          rules={c.rules}
          problems={visible}
          intraday={intraday}
          onAddPick={b.picks.addPick}
          signals={signalNames}
          prefix={`${prefix}rule`}
          title="Entry signal"
          label="Entry when"
          onChange={c.setRules}
        />
        {/* Older strategies may have an any-of list; AND/OR joins replace it, but its rules
            stay visible and editable until removed. */}
        {c.anyRules.length > 0 && (
          <RulesEditor
            key={`any-${c.active}`}
            catalog={catalog}
            picked={picked}
            rules={c.anyRules}
            problems={visible}
            intraday={intraday}
            onAddPick={b.picks.addPick}
            signals={signalNames}
            prefix={`${prefix}any-rule`}
            title="Any one of these"
            label="And any of"
            onChange={c.setAnyRules}
          />
        )}
        {shared ? (
          <div className="flex flex-col gap-2 rounded-md border border-l-[3px] border-line border-l-down/70 bg-surface px-3 py-2.5 sm:flex-row sm:items-center">
            <span className="shrink-0 text-[13px] font-semibold text-down sm:w-24">
              Exit when
            </span>
            <span className="flex-1 text-[13px] text-ink-2">
              Same as Case 1
              {caseOneExits.length
                ? `: ${caseOneExits.map((rule) => ruleText(catalog, rule)).join("; or ")}`
                : " (none)"}
            </span>
          </div>
        ) : (
          <RulesEditor
            key={`exit-${c.active}`}
            catalog={catalog}
            picked={picked}
            rules={c.exitRules}
            problems={visible}
            intraday={intraday}
            onAddPick={b.picks.addPick}
            signals={signalNames}
            prefix={`${prefix}exit-rule`}
            title="Exit signal"
            label="Exit when"
            tone="exit"
            onChange={c.setExitRules}
          />
        )}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Hint>
            {intraday
              ? "Checked as each candle closes; a trade enters at the next check."
              : "Read at the close; a trade enters at the next open."}{" "}
            Empty entry means only the filters decide. Exit when closes the
            trade early; the targets and timing below apply too.
          </Hint>
          {!own && (
            <Switch
              id={`case-${index}-same-exits`}
              checked={shared}
              onChange={(on) =>
                c.setCase(index, { exit_rules: on ? null : [] })
              }
            >
              Same exit as Case 1
            </Switch>
          )}
        </div>
      </div>

      <Block
        title="Legs"
        hint="The options this case buys or sells when its signal holds."
        aside={
          <Badge>
            {c.legs.length} of {MAX_LEGS}
          </Badge>
        }
      >
        <LegsEditor
          key={`legs-${c.active}`}
          legs={c.legs}
          prefix={`${prefix}leg`}
          problems={visible}
          onChange={c.setLegs}
        />
      </Block>
    </div>
  );
}

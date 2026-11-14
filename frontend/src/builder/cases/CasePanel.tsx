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

/** The open case: its name and view (after Case 1), entry signal, legs and exit signal. */
export function CasePanel({ b }: { b: ReadyBuilder }) {
  const { cases: c, visible, intraday, lab } = b;
  const catalog = lab.technical;
  const own = c.active === 0;
  const index = c.active - 1;
  const prefix = own ? "" : `case-${index}-`;
  const picked = b.picks.picks;
  const shared = c.item?.exit_rules == null && !own;
  const caseOneExits = b.spec.exits.rules ?? [];

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
          Basics. Add a case for another way into the trade, such as the put
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

      <Block
        title="Entry signal"
        tone="entry"
        hint={
          intraday
            ? "Rules on the underlying, on 5-minute to monthly candles. A trade enters at the next check."
            : "Rules on the underlying, on daily to monthly candles, read at the close. The trade enters at the next open."
        }
      >
        <RulesEditor
          key={`entry-${c.active}`}
          joins
          catalog={catalog}
          picked={picked}
          rules={c.rules}
          problems={visible}
          intraday={intraday}
          prefix={`${prefix}rule`}
          title="Entry signal"
          onChange={c.setRules}
        />
        {/* Older strategies may have an any-of list; AND/OR joins replace it, but its rules
            stay visible and editable until removed. */}
        {c.anyRules.length > 0 && (
          <>
            <h4 className="mt-4 mb-1.5 text-[12.5px] font-semibold text-ink-2">
              And any one of these
            </h4>
            <RulesEditor
              key={`any-${c.active}`}
              catalog={catalog}
              picked={picked}
              rules={c.anyRules}
              problems={visible}
              intraday={intraday}
              prefix={`${prefix}any-rule`}
              title="Any one of these"
              onChange={c.setAnyRules}
            />
          </>
        )}
        <Hint className="mt-3">
          Join rules with AND or OR; AND is checked first. With no rules, only
          the Entry filters decide.
        </Hint>
      </Block>

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

      <Block
        title="Exit signal"
        tone="exit"
        hint="Optional. Closes this case's trade early when any of these holds, checked at each decision. The target, stop and time exits under Exits and stops apply as well."
      >
        {!own && (
          <div className="mb-3">
            <Switch
              id={`case-${index}-same-exits`}
              checked={shared}
              onChange={(on) =>
                c.setCase(index, { exit_rules: on ? null : [] })
              }
            >
              Same exit rules as Case 1
            </Switch>
          </div>
        )}
        {shared ? (
          <Hint>
            {caseOneExits.length
              ? caseOneExits
                  .map((rule) => ruleText(catalog, rule))
                  .join("; or ")
              : "Case 1 has no exit rules."}
          </Hint>
        ) : (
          <RulesEditor
            key={`exit-${c.active}`}
            catalog={catalog}
            picked={picked}
            rules={c.exitRules}
            problems={visible}
            intraday={intraday}
            prefix={`${prefix}exit-rule`}
            title="Exit signal"
            onChange={c.setExitRules}
          />
        )}
      </Block>
    </div>
  );
}

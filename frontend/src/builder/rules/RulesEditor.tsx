import { Pencil, Plus } from "lucide-react";
import { Fragment, useState } from "react";
import type { IndicatorPick, RuleJoin, TechCatalog, TechRule } from "../../api";
import { MAX_RULES, MINUTE_FRAMES } from "../logic/picks";
import { ruleHelpers } from "../logic/ruleDefaults";
import { ruleProblems, ruleText } from "../logic/ruleText";
import { Button, cx, Dialog, Select } from "../../components/ui";
import { ConditionRow, ROW_GRID } from "./ConditionRow";

/** A signal: its conditions as sentences, and one pop-up that edits them all, a condition
 * per row with AND or OR between rows. Conditions read prices, the strategy's indicators
 * and its candle patterns. */
export function RulesEditor({
  catalog,
  rules,
  problems,
  intraday,
  picked,
  onChange,
  prefix = "rule",
  title = "Signal",
  joins = false,
}: {
  catalog: TechCatalog;
  rules: TechRule[];
  problems: Record<string, string>;
  /** Ready-made conditions on minute candles are offered only to intraday strategies. */
  intraday: boolean;
  picked: IndicatorPick[];
  onChange: (rules: TechRule[]) => void;
  /** Field ids start with this, so entry and exit signals do not collide. */
  prefix?: string;
  /** The pop-up's title, such as "Entry signal". */
  title?: string;
  /** AND or OR between rows. Otherwise the signal holds when any one condition does. */
  joins?: boolean;
}) {
  const [draft, setDraft] = useState<TechRule[] | null>(null);
  const hasProblem = (index: number) =>
    Object.keys(problems).some(
      (key) =>
        key === `${prefix}-${index}` || key.startsWith(`${prefix}-${index}-`),
    );
  const word = (index: number) =>
    !joins || rules[index].join === "or" ? "OR" : "AND";
  const open = () =>
    setDraft(
      rules.length
        ? structuredClone(rules)
        : [ruleHelpers(catalog, picked).newCompare({ timeframe: "1d" })],
    );

  return (
    <div className="flex flex-col gap-2">
      {rules.length > 0 ? (
        <ol className="flex flex-col gap-1.5">
          {rules.map((rule, index) => (
            <li
              key={index}
              id={`${prefix}-${index}`}
              data-problem={hasProblem(index) || undefined}
              className="flex items-baseline gap-2 text-[13px]"
            >
              {index > 0 && <JoinWord word={word(index)} />}
              <span className={hasProblem(index) ? "text-down" : "text-ink"}>
                {ruleText(catalog, rule)}
                {hasProblem(index) && " (needs fixing)"}
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-[13px] text-ink-3">No conditions yet.</p>
      )}
      <div>
        <Button icon={rules.length ? Pencil : Plus} onClick={open}>
          {rules.length ? "Edit signal" : "Add signal"}
        </Button>
      </div>
      {draft && (
        <SignalDialog
          title={title}
          catalog={catalog}
          picked={picked}
          intraday={intraday}
          joins={joins}
          prefix={`${prefix}-draft`}
          initial={draft}
          hadRules={rules.length > 0}
          onSave={(next) => {
            onChange(next);
            setDraft(null);
          }}
          onClose={() => setDraft(null)}
        />
      )}
    </div>
  );
}

/** Every condition of a signal as rows, edited together and saved at once. */
function SignalDialog({
  title,
  catalog,
  picked,
  intraday,
  joins,
  prefix,
  initial,
  hadRules,
  onSave,
  onClose,
}: {
  title: string;
  catalog: TechCatalog;
  picked: IndicatorPick[];
  intraday: boolean;
  joins: boolean;
  prefix: string;
  initial: TechRule[];
  hadRules: boolean;
  onSave: (rules: TechRule[]) => void;
  onClose: () => void;
}) {
  const [rows, setRows] = useState(initial);
  const helpers = ruleHelpers(catalog, picked);
  const problems = ruleProblems(catalog, rows, prefix);
  const blocked = Object.keys(problems).length > 0;
  const offered = helpers.presets.filter(
    (preset) => intraday || !MINUTE_FRAMES.includes(preset.rule.timeframe),
  );
  const room = rows.length < MAX_RULES;
  const add = (rule: TechRule) => setRows([...rows, structuredClone(rule)]);
  const set = (index: number, rule: TechRule) =>
    setRows(rows.map((item, i) => (i === index ? rule : item)));
  const remove = (index: number) => {
    const next = rows.filter((_, i) => i !== index);
    // The first row joins nothing; one that moves up drops its join.
    if (next[0]?.join) next[0] = { ...next[0], join: undefined };
    setRows(next);
  };

  return (
    <Dialog
      title={title}
      description={
        joins
          ? "Each row is one condition. Join rows with AND or OR; AND is checked first."
          : "Each row is one condition. The signal holds when any one of them does."
      }
      size="xl"
      as="form"
      onSubmit={() => {
        if (!blocked) onSave(rows);
      }}
      onClose={onClose}
      footer={
        <>
          {hadRules && (
            <Button
              variant="danger"
              className="mr-auto"
              onClick={() => onSave([])}
            >
              Delete signal
            </Button>
          )}
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={blocked}>
            Save
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {rows.length > 0 && (
          <div
            aria-hidden
            className={cx(
              ROW_GRID,
              "hidden text-[12px] font-semibold text-ink-3 md:grid",
            )}
          >
            <span>Value</span>
            <span>Condition</span>
            <span>Compared with</span>
            <span />
          </div>
        )}
        {rows.map((rule, index) => (
          <Fragment key={index}>
            {index > 0 &&
              (joins ? (
                <JoinToggle
                  value={rule.join ?? "and"}
                  onChange={(join) => set(index, { ...rule, join })}
                />
              ) : (
                <div className="flex justify-center">
                  <JoinWord word="OR" />
                </div>
              ))}
            <ConditionRow
              id={`${prefix}-${index}`}
              index={index}
              rule={rule}
              catalog={catalog}
              picked={picked}
              helpers={helpers}
              problems={problems}
              onChange={(next) => set(index, next)}
              onRemove={() => remove(index)}
            />
          </Fragment>
        ))}
        {!rows.length && (
          <p className="text-[13px] text-ink-3">
            No conditions. Saving leaves the signal empty.
          </p>
        )}
        {room ? (
          <div className="flex flex-wrap items-center justify-center gap-2 pt-1">
            <Button
              variant="primary"
              icon={Plus}
              onClick={() => add(helpers.newCompare({ timeframe: "1d" }))}
            >
              Add condition
            </Button>
            {offered.length > 0 && (
              <Select
                className="w-auto"
                aria-label="Add a ready-made condition"
                value=""
                onChange={(event) => {
                  const preset = offered[Number(event.target.value)];
                  if (preset) add(preset.rule);
                }}
              >
                <option value="">Add a ready-made condition…</option>
                {offered.map((preset, i) => (
                  <option key={preset.label} value={i}>
                    {preset.label}
                  </option>
                ))}
              </Select>
            )}
          </div>
        ) : (
          <p className="text-center text-[12.5px] text-ink-3">
            That is the most conditions a signal can hold ({MAX_RULES}).
          </p>
        )}
      </div>
    </Dialog>
  );
}

function JoinWord({ word }: { word: string }) {
  return (
    <span className="rounded-sm bg-surface-2 px-1.5 py-px text-[11px] font-semibold text-ink-2">
      {word}
    </span>
  );
}

/** AND or OR between two rows: how the row below joins the rows above it. */
function JoinToggle({
  value,
  onChange,
}: {
  value: RuleJoin;
  onChange: (join: RuleJoin) => void;
}) {
  return (
    <div
      className="flex justify-center"
      role="radiogroup"
      aria-label="How the next condition joins"
    >
      <div className="inline-flex overflow-hidden rounded-full border border-accent">
        {(["and", "or"] as const).map((join) => (
          <button
            key={join}
            type="button"
            role="radio"
            aria-checked={value === join}
            onClick={() => onChange(join)}
            className={
              value === join
                ? "w-20 bg-accent py-1 text-[12.5px] font-semibold text-white"
                : "w-20 bg-surface py-1 text-[12.5px] font-medium text-ink-2 hover:bg-hover"
            }
          >
            {join === "and" ? "AND" : "OR"}
          </button>
        ))}
      </div>
    </div>
  );
}

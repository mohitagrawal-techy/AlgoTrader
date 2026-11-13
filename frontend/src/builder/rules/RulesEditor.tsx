import { Plus } from "lucide-react";
import type { IndicatorPick, TechCatalog, TechRule } from "../../api";
import { MAX_RULES, MINUTE_FRAMES } from "../logic/picks";
import { ruleHelpers } from "../logic/ruleDefaults";
import { Button } from "../ui/Button";
import { Select } from "../ui/inputs";
import { RuleCard } from "./RuleCard";

/** A list of rules on the underlying, with ready-made ones a click away. Rules choose from
 * prices and the strategy's picked indicators. */
export function RulesEditor({
  catalog,
  rules,
  problems,
  intraday,
  picked,
  onChange,
  prefix = "rule",
  addLabel = "Add a rule",
  quickAdd = false,
}: {
  catalog: TechCatalog;
  rules: TechRule[];
  problems: Record<string, string>;
  /** Minute timeframes are offered only to strategies that decide during the day. */
  intraday: boolean;
  picked: IndicatorPick[];
  onChange: (rules: TechRule[]) => void;
  /** Field ids start with this, so entry and exit rules do not collide. */
  prefix?: string;
  addLabel?: string;
  /** Show the ready-made rules as one-click buttons above the list. */
  quickAdd?: boolean;
}) {
  const helpers = ruleHelpers(catalog, picked);
  const frames = catalog.timeframes.filter(
    (frame) =>
      intraday ||
      !MINUTE_FRAMES.includes(frame.value) ||
      rules.some((r) => r.timeframe === frame.value),
  );
  const offered = helpers.presets.filter(
    (preset) => intraday || !MINUTE_FRAMES.includes(preset.rule.timeframe),
  );
  const room = rules.length < MAX_RULES;
  const add = (rule: TechRule) => onChange([...rules, structuredClone(rule)]);

  return (
    <div className="flex flex-col gap-2">
      {quickAdd && room && offered.length > 0 && (
        <div
          className="flex flex-wrap gap-1.5"
          role="group"
          aria-label="Add a common indicator rule"
        >
          {offered.map((preset) => (
            <Button
              key={preset.label}
              size="sm"
              icon={Plus}
              className="rounded-full font-medium"
              onClick={() => add(preset.rule)}
            >
              {preset.label}
            </Button>
          ))}
        </div>
      )}
      {rules.length > 0 && (
        <ol className="flex flex-col gap-2">
          {rules.map((rule, index) => (
            <RuleCard
              key={index}
              id={`${prefix}-${index}`}
              index={index}
              rule={rule}
              catalog={catalog}
              picked={picked}
              helpers={helpers}
              frames={frames}
              problems={problems}
              onChange={(next) =>
                onChange(rules.map((item, i) => (i === index ? next : item)))
              }
              onRemove={() => onChange(rules.filter((_, i) => i !== index))}
            />
          ))}
        </ol>
      )}
      {room ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            icon={Plus}
            onClick={() => add(helpers.newCompare({ timeframe: "1d" }))}
          >
            {addLabel}
          </Button>
          {!quickAdd && helpers.presets.length > 0 && (
            <Select
              small
              className="w-auto"
              aria-label="Add a ready-made rule"
              value=""
              onChange={(event) => {
                const preset = helpers.presets[Number(event.target.value)];
                if (preset) add(preset.rule);
              }}
            >
              <option value="">Add a ready-made rule…</option>
              {helpers.presets.map((preset, i) => (
                <option key={preset.label} value={i}>
                  {preset.label}
                </option>
              ))}
            </Select>
          )}
        </div>
      ) : (
        <p className="text-[12.5px] text-ink-3">
          That is the most rules a list can hold ({MAX_RULES}).
        </p>
      )}
    </div>
  );
}

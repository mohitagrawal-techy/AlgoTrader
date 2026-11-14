import { CircleAlert, Plus } from "lucide-react";
import type { LabLeg } from "../../api";
import { MAX_LEGS, NEW_LEG } from "../logic/spec";
import { Button, cx, Hint } from "../../components/ui";
import { LEG_COLUMNS, LEG_HEADINGS, LegRow } from "./LegRow";

/** The options a case trades, one row each, with every leg's problems listed below. */
export function LegsEditor({
  legs,
  problems,
  onChange,
  prefix = "leg",
}: {
  legs: LabLeg[];
  problems: Record<string, string>;
  onChange: (legs: LabLeg[]) => void;
  prefix?: string;
}) {
  const found = legs.flatMap((_, index) =>
    ["ratio", "value", "stop", "target"]
      .map((field) => problems[`${prefix}-${index}-${field}`])
      .filter(Boolean)
      .map((text) => ({ index, text })),
  );
  return (
    <div className="@container flex flex-col gap-3">
      <div
        aria-hidden
        className={cx(
          "hidden gap-x-3 px-3 text-[11.5px] font-semibold text-ink-3 @4xl:grid",
          LEG_COLUMNS,
        )}
      >
        {LEG_HEADINGS.map((heading) => (
          <span key={heading}>{heading}</span>
        ))}
        <span />
      </div>
      <ol className="flex flex-col gap-4 pt-1 @4xl:gap-3">
        {legs.map((leg, index) => (
          <LegRow
            key={index}
            id={`${prefix}-${index}`}
            index={index}
            leg={leg}
            problems={problems}
            removable={legs.length > 1}
            onChange={(change) =>
              onChange(
                legs.map((item, i) =>
                  i === index ? { ...item, ...change } : item,
                ),
              )
            }
            onRemove={() => onChange(legs.filter((_, i) => i !== index))}
          />
        ))}
      </ol>
      {found.length > 0 && (
        <ul className="flex flex-col gap-1" role="alert">
          {found.map(({ index, text }) => (
            <li
              key={`${index}-${text}`}
              className="flex items-start gap-1 text-[12.5px] text-down"
            >
              <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" />
              Leg {index + 1}: {text}
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Hint className="max-w-prose">
          Lots set the ratio between legs; the budget decides how many sets to
          trade. A leg's own stop loss or take profit closes only that leg.
        </Hint>
        {legs.length < MAX_LEGS && (
          <Button
            icon={Plus}
            onClick={() => onChange([...legs, { ...NEW_LEG, value: 0.3 }])}
          >
            Add leg
          </Button>
        )}
      </div>
    </div>
  );
}

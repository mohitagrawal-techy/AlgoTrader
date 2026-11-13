import { CircleAlert } from "lucide-react";
import type { LabCase } from "../../api";
import { cx } from "../ui/Button";

/** The cases as tabs. Arrow keys move between them; a tab whose fields need fixing is
 * marked, with words for screen readers as well as the icon. */
export function CaseTabs({
  cases,
  active,
  withProblems,
  onSelect,
}: {
  cases: LabCase[];
  active: number;
  withProblems: Set<number>;
  onSelect: (index: number) => void;
}) {
  const count = 1 + cases.length;
  const name = (index: number) =>
    index === 0
      ? "Case 1"
      : cases[index - 1].name.trim() || `Case ${index + 1}`;
  return (
    <div
      role="tablist"
      aria-label="Cases"
      className="-mb-px flex max-w-full gap-1 overflow-x-auto"
    >
      {Array.from({ length: count }, (_, index) => {
        const selected = index === active;
        const problem = withProblems.has(index);
        return (
          <button
            key={index}
            type="button"
            role="tab"
            id={`case-tab-${index}`}
            aria-selected={selected}
            aria-controls="case-panel"
            tabIndex={selected ? 0 : -1}
            onClick={() => onSelect(index)}
            onKeyDown={(event) => {
              const step =
                event.key === "ArrowRight"
                  ? 1
                  : event.key === "ArrowLeft"
                    ? -1
                    : 0;
              if (!step) return;
              event.preventDefault();
              const next = (index + step + count) % count;
              onSelect(next);
              document.getElementById(`case-tab-${next}`)?.focus();
            }}
            className={cx(
              "inline-flex max-w-56 shrink-0 cursor-pointer items-center gap-1.5 rounded-t-md border border-b-0 px-3.5 py-2",
              "text-[13px] font-semibold whitespace-nowrap",
              selected
                ? "border-line bg-surface text-ink"
                : "border-transparent text-ink-3 hover:text-ink",
              problem && "text-down",
            )}
          >
            <span className="overflow-hidden text-ellipsis whitespace-nowrap">
              {name(index)}
            </span>
            {problem && (
              <>
                <CircleAlert aria-hidden className="size-3.5 shrink-0" />
                <span className="sr-only">, needs fixing</span>
              </>
            )}
          </button>
        );
      })}
    </div>
  );
}

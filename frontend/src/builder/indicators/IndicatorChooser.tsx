import { Search } from "lucide-react";
import { useEffect, useRef } from "react";
import { IndicatorTip } from "../../views/IndicatorTip";
import { cx } from "../ui/Button";
import { CONTROL } from "../ui/inputs";
import type { IndicatorFormState } from "./useIndicatorForm";

/** The searchable list of indicators, grouped by type. Enter in the search box picks the
 * first match. */
export function IndicatorChooser({ form }: { form: IndicatorFormState }) {
  const search = useRef<HTMLInputElement>(null);
  useEffect(() => search.current?.focus(), []);
  const { groups, chosen, query } = form;
  return (
    <div className="flex min-h-0 flex-col gap-2 md:w-64 md:shrink-0">
      <label htmlFor="indicator-search" className="sr-only">
        Find an indicator
      </label>
      <div className="relative">
        <Search
          aria-hidden
          className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-ink-3"
        />
        <input
          id="indicator-search"
          ref={search}
          type="search"
          className={cx(CONTROL, "pl-8")}
          placeholder="Find an indicator, for example RSI"
          value={query}
          onChange={(event) => form.setQuery(event.target.value)}
          onKeyDown={(event) => {
            // Enter picks the first match rather than submitting an empty form.
            const first = groups[0]?.items[0];
            if (event.key === "Enter" && !chosen && first) {
              event.preventDefault();
              form.choose(first);
            }
          }}
        />
      </div>
      <div
        role="listbox"
        aria-label="Indicators"
        className="max-h-56 min-h-0 overflow-y-auto rounded-md border border-line p-1 md:max-h-[52vh]"
      >
        {groups.map((group) => (
          <div key={group.title} role="group" aria-label={group.title}>
            <div className="px-2 pt-2 pb-1 text-[11.5px] font-semibold text-ink-3">
              {group.title}
            </div>
            {group.items.map((item) => (
              <IndicatorTip
                key={item.name}
                indicator={item}
                className="flex w-full"
              >
                <button
                  type="button"
                  role="option"
                  aria-selected={item.name === chosen?.name}
                  onClick={() => form.choose(item)}
                  className={cx(
                    "flex w-full cursor-pointer items-center justify-between gap-2 rounded-sm px-2 py-1.5 text-left",
                    "text-[13px] text-ink hover:bg-hover",
                    item.name === chosen?.name &&
                      "bg-accent-wash font-semibold",
                  )}
                >
                  <span>{item.label}</span>
                  {item.intraday_only && (
                    <small className="text-[11px] text-ink-3">intraday</small>
                  )}
                </button>
              </IndicatorTip>
            ))}
          </div>
        ))}
        {!groups.length && (
          <p className="px-2 py-3 text-[12.5px] text-ink-3">
            No indicator matches "{query}".
          </p>
        )}
      </div>
    </div>
  );
}

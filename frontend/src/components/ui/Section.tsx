import { ChevronRight } from "lucide-react";
import { createContext, useContext, type ReactNode } from "react";
import { cx } from "./Button";

/** Which sections are folded, by title, shared so one control can fold them all. */
export const FoldContext = createContext<{
  folded: Set<string>;
  toggle: (title: string) => void;
}>({
  folded: new Set(),
  toggle: () => undefined,
});

/** A card that folds to its title. Folded content is hidden, not removed, so a chart keeps
 * its data and a half-typed value its text. */
export function Section({
  title,
  hint,
  aside,
  children,
}: {
  title: string;
  hint?: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  const { folded, toggle } = useContext(FoldContext);
  const open = !folded.has(title);
  const bodyId = `section-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <section className="rounded-lg border border-line bg-surface shadow-card">
      <div
        className={cx(
          "flex items-center justify-between gap-3 px-4 py-3",
          open && "border-b border-line",
        )}
      >
        <button
          type="button"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => toggle(title)}
          className="group flex min-w-0 flex-1 cursor-pointer items-start gap-2 rounded-sm text-left"
        >
          <ChevronRight
            aria-hidden
            className={cx(
              "mt-0.5 size-4 shrink-0 text-ink-3 transition-transform group-hover:text-ink motion-reduce:transition-none",
              open && "rotate-90",
            )}
          />
          <span className="min-w-0">
            <h2 className="text-[15px] font-semibold text-ink [font-stretch:88%]">
              {title}
            </h2>
            {hint && open && (
              <span className="mt-0.5 block text-[12.5px] leading-snug text-ink-3">
                {hint}
              </span>
            )}
          </span>
        </button>
        {aside}
      </div>
      <div id={bodyId} hidden={!open} className="p-4">
        {children}
      </div>
    </section>
  );
}

const BADGE_TONES = {
  neutral: "bg-surface-2 text-ink-2",
  accent: "bg-accent-wash text-ink",
  warning: "bg-warning-wash text-ink",
  critical: "bg-critical-wash text-down",
  call: "bg-call/15 text-ink",
  put: "bg-put/15 text-ink",
};

/** A short count or label beside a title. */
export function Badge({
  tone = "neutral",
  children,
  className,
}: {
  tone?: keyof typeof BADGE_TONES;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cx(
        "inline-flex h-5 shrink-0 items-center rounded-full px-2 text-[11.5px] font-semibold tabular-nums",
        BADGE_TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** A small titled block inside a section, such as a case's entry rules. */
export function Block({
  title,
  hint,
  aside,
  tone,
  children,
}: {
  title: string;
  hint?: string;
  aside?: ReactNode;
  tone?: "entry" | "exit";
  children: ReactNode;
}) {
  return (
    <div
      className={cx(
        "rounded-md border border-line bg-surface p-3",
        tone === "entry" && "border-l-3 border-l-up",
        tone === "exit" && "border-l-3 border-l-down",
      )}
    >
      <div className="mb-2.5 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="text-[13.5px] font-semibold text-ink">{title}</h3>
        {hint && <p className="text-[12.5px] text-ink-3">{hint}</p>}
        {aside}
      </div>
      {children}
    </div>
  );
}

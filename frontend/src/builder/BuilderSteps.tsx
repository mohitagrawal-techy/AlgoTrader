import { useContext, useLayoutEffect, useState } from "react";
import { cx, FoldContext, sectionId } from "../components/ui";

/** The builder's sections in the order a strategy is defined, each with what it settles. */
/** `title` is the section's own; `label` is the shorter name the bar shows. */
export const STEPS = [
  { title: "Basics", label: "Basics", about: "Index, view, timing" },
  { title: "Indicators", label: "Indicators", about: "What rules read" },
  { title: "Cases", label: "Cases", about: "Signals and legs" },
  { title: "Entry filters", label: "Filters", about: "When it may enter" },
  { title: "Exits and stops", label: "Exits", about: "How it closes" },
  { title: "Sizing and risk", label: "Sizing", about: "Capital and lots" },
] as const;

const PROBLEM = '[aria-invalid="true"], [data-problem]';

/** A bar of the steps: where each section is, and which have something to fix. Clicking
 * one opens that section and scrolls to it. */
export function BuilderSteps() {
  const { folded, toggle } = useContext(FoldContext);
  const [problems, setProblems] = useState<boolean[]>([]);

  // Read after every render: the builder re-renders on each edit, and fields mark
  // themselves invalid.
  useLayoutEffect(() => {
    const found = STEPS.map(
      (step) =>
        !!document
          .getElementById(`${sectionId(step.title)}-card`)
          ?.querySelector(PROBLEM),
    );
    if (found.some((value, i) => value !== problems[i])) setProblems(found);
  });

  const go = (title: string) => {
    if (folded.has(title)) toggle(title);
    const reduce = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    // After the section opens.
    window.requestAnimationFrame(() => {
      const card = document.getElementById(`${sectionId(title)}-card`);
      card?.scrollIntoView({
        behavior: reduce ? "auto" : "smooth",
        block: "start",
      });
      card?.querySelector<HTMLElement>("button[aria-expanded]")?.focus({
        preventScroll: true,
      });
    });
  };

  return (
    <nav
      aria-label="Strategy steps"
      // Sticks just under the app's top bar.
      className="sticky top-[var(--bar-h)] z-10 -mx-1 bg-page/95 px-1 py-1.5 backdrop-blur-sm"
    >
      <ol className="grid grid-cols-3 gap-1.5 md:grid-cols-6">
        {STEPS.map((step, index) => {
          const problem = problems[index];
          return (
            <li key={step.title} className="min-w-0">
              <button
                type="button"
                title={`${step.title}: ${step.about}`}
                onClick={() => go(step.title)}
                className={cx(
                  "flex w-full min-w-0 cursor-pointer items-center gap-2 rounded-md border bg-surface px-2.5 py-1.5 text-left",
                  "hover:border-accent focus-visible:outline-2 focus-visible:outline-accent",
                  problem ? "border-down" : "border-line",
                )}
              >
                <span
                  aria-hidden
                  className={cx(
                    "grid size-5 shrink-0 place-items-center rounded-full text-[11.5px] font-semibold tabular-nums",
                    problem
                      ? "bg-critical-wash text-down"
                      : "bg-accent-wash text-ink",
                  )}
                >
                  {index + 1}
                </span>
                <span className="flex min-w-0 flex-col leading-tight">
                  <span className="truncate text-[12.5px] font-semibold text-ink">
                    {step.label}
                    {problem && <span className="sr-only">, needs fixing</span>}
                  </span>
                  <span className="hidden truncate text-[11.5px] text-ink-3 2xl:block">
                    {step.about}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

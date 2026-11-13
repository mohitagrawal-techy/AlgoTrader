import type { ReactNode } from "react";
import { cx } from "../components/ui";

/** A flat desk panel: a one-line title bar over content that runs to the edges. */
export function DeskPanel({
  title,
  aside,
  className,
  children,
}: {
  title: string;
  aside?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-label={title}
      className={cx(
        "flex min-h-0 min-w-0 flex-1 flex-col rounded-lg border border-line bg-surface",
        className,
      )}
    >
      <header className="flex h-9 shrink-0 items-center justify-between gap-3 border-b border-line px-3">
        <h2 className="text-[12.5px] font-semibold text-ink">{title}</h2>
        {aside && (
          <div className="flex min-w-0 items-center gap-2 text-[11.5px] text-ink-3">
            {aside}
          </div>
        )}
      </header>
      <div className="min-h-0 flex-1 overflow-auto">{children}</div>
    </section>
  );
}

/** A quiet line in an empty panel, saying what will appear and when. */
export function DeskEmpty({ children }: { children: ReactNode }) {
  return (
    <p className="px-3 py-6 text-center text-[12.5px] text-ink-3">{children}</p>
  );
}

/** Grey bars in a panel's place while its data loads. */
export function DeskSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col gap-2 p-3">
      {Array.from({ length: rows }, (_, index) => (
        <span
          key={index}
          className="h-4 animate-pulse rounded-sm bg-surface-2 motion-reduce:animate-none"
        />
      ))}
    </div>
  );
}

import type { ReactNode } from "react";

/** A titled part of a section, separated from the part above by a rule. */
export function Group({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="border-t border-line pt-4 first:border-t-0 first:pt-0">
      <h3 className="text-[13.5px] font-semibold text-ink">{title}</h3>
      {hint && (
        <p className="mt-0.5 mb-3 max-w-[72ch] text-[12.5px] leading-snug text-ink-3">
          {hint}
        </p>
      )}
      {!hint && <div className="mb-3" />}
      {children}
    </div>
  );
}

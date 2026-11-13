import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";

/** A quiet link back to the page this one was opened from, above the page's title. */
export function BackLink({
  href,
  children,
}: {
  href: string;
  children: ReactNode;
}) {
  return (
    <a
      href={href}
      className="inline-flex items-center gap-1 rounded-sm text-[12.5px] font-medium text-ink-3 no-underline hover:text-ink"
    >
      <ArrowLeft aria-hidden className="size-3.5" />
      {children}
    </a>
  );
}

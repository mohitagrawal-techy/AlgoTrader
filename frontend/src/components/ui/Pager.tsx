import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react";
import { Button } from "./Button";
import { Select } from "./inputs";

export const PAGE_SIZES = [25, 50, 100] as const;

/** Rows `page` (from 0) of a list shows, and how many pages there are. */
export function pageOf<T>(
  rows: T[],
  page: number,
  size: number,
): { shown: T[]; page: number; pages: number } {
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const at = Math.min(Math.max(page, 0), pages - 1);
  return { shown: rows.slice(at * size, at * size + size), page: at, pages };
}

/** Moves through a long table a page at a time: "Showing 26–50 of 208 trades". */
export function Pager({
  page,
  pages,
  size,
  total,
  noun,
  onPage,
  onSize,
}: {
  /** The current page, from 0. */
  page: number;
  pages: number;
  size: number;
  total: number;
  /** What a row is, for the count: "trade". */
  noun: string;
  onPage: (page: number) => void;
  onSize: (size: number) => void;
}) {
  const from = total === 0 ? 0 : page * size + 1;
  const to = Math.min(total, (page + 1) * size);
  return (
    <nav
      aria-label={`${noun} pages`}
      className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-3.5 py-2.5 text-[13px] text-ink-2"
    >
      <span aria-live="polite">
        Showing {from}–{to} of {total} {noun}
        {total === 1 ? "" : "s"}
      </span>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2">
          <span>Rows</span>
          <Select
            small
            value={size}
            onChange={(event) => onSize(Number(event.target.value))}
          >
            {PAGE_SIZES.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </Select>
        </label>
        <Button
          variant="ghost"
          size="icon"
          icon={ChevronsLeft}
          aria-label="First page"
          disabled={page === 0}
          onClick={() => onPage(0)}
        />
        <Button
          variant="ghost"
          size="icon"
          icon={ChevronLeft}
          aria-label="Previous page"
          disabled={page === 0}
          onClick={() => onPage(page - 1)}
        />
        <span className="tabular-nums">
          Page {page + 1} of {pages}
        </span>
        <Button
          variant="ghost"
          size="icon"
          icon={ChevronRight}
          aria-label="Next page"
          disabled={page >= pages - 1}
          onClick={() => onPage(page + 1)}
        />
        <Button
          variant="ghost"
          size="icon"
          icon={ChevronsRight}
          aria-label="Last page"
          disabled={page >= pages - 1}
          onClick={() => onPage(pages - 1)}
        />
      </div>
    </nav>
  );
}

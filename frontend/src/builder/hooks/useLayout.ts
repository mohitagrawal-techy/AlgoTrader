import { useMemo, useState } from "react";

const SECTIONS_KEY = "algoverse.builder.collapsed";
const SUMMARY_KEY = "algoverse.builder.summary";

export const SECTION_TITLES = [
  "Start from",
  "Basics",
  "Budget",
  "Indicators",
  "Features",
  "Cases",
  "Enter when",
  "Exit when",
];

function read<T>(
  key: string,
  parse: (raw: string | null) => T,
  fallback: T,
): T {
  try {
    return parse(localStorage.getItem(key));
  } catch {
    return fallback;
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Remembered for this visit only when storage is blocked.
  }
}

/** Which sections are folded and whether the summary is open, remembered in this browser. */
export function useLayout() {
  const [folded, setFolded] = useState<Set<string>>(() =>
    read(
      SECTIONS_KEY,
      (raw) => new Set(JSON.parse(raw ?? "[]") as string[]),
      new Set<string>(),
    ),
  );
  const [summaryOpen, setSummaryOpen] = useState(() =>
    read(SUMMARY_KEY, (raw) => raw !== "closed", true),
  );

  const saveFolded = (next: Set<string>) => {
    setFolded(next);
    write(SECTIONS_KEY, JSON.stringify([...next]));
  };
  const fold = useMemo(
    () => ({
      folded,
      toggle: (title: string) => {
        const next = new Set(folded);
        if (next.has(title)) next.delete(title);
        else next.add(title);
        saveFolded(next);
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [folded],
  );

  return {
    fold,
    collapseAll: () => saveFolded(new Set(SECTION_TITLES)),
    expandAll: () => saveFolded(new Set()),
    summaryOpen,
    setSummary: (open: boolean) => {
      setSummaryOpen(open);
      write(SUMMARY_KEY, open ? "open" : "closed");
    },
  };
}

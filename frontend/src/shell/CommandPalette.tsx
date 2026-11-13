import { CornerDownLeft, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Dialog, cx } from "../components/ui";

export interface Command {
  id: string;
  label: string;
  group: string;
  hint?: string;
  run: () => void;
}

/** ⌘K or Ctrl K: type to filter, arrows to move, Enter to go. */
export function CommandPalette({
  commands,
  onClose,
}: {
  commands: Command[];
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const list = useRef<HTMLUListElement>(null);
  const matches = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    return commands.filter((command) => {
      const text =
        `${command.group} ${command.label} ${command.hint ?? ""}`.toLowerCase();
      return words.every((word) => text.includes(word));
    });
  }, [commands, query]);
  const current = Math.min(active, Math.max(0, matches.length - 1));

  useEffect(() => {
    list.current
      ?.querySelector(`[data-index="${current}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [current]);

  const go = (command: Command | undefined) => {
    if (!command) return;
    onClose();
    command.run();
  };

  return (
    <Dialog title="Go to" size="md" flush onClose={onClose}>
      <div className="flex flex-col">
        <label className="flex items-center gap-2 border-b border-line px-4">
          <Search aria-hidden className="size-4 shrink-0 text-ink-3" />
          <input
            data-autofocus
            role="combobox"
            aria-expanded
            aria-controls="command-list"
            aria-activedescendant={
              matches[current] ? `command-${matches[current].id}` : undefined
            }
            aria-label="Search pages and actions"
            className="h-11 min-w-0 flex-1 bg-transparent text-[14px] text-ink outline-none placeholder:text-ink-3"
            placeholder="Search pages and actions"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setActive(Math.min(current + 1, matches.length - 1));
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setActive(Math.max(current - 1, 0));
              } else if (event.key === "Enter") {
                event.preventDefault();
                go(matches[current]);
              }
            }}
          />
        </label>
        <ul
          id="command-list"
          role="listbox"
          ref={list}
          aria-label="Results"
          className="max-h-[50vh] overflow-y-auto py-1"
        >
          {matches.length === 0 && (
            <li className="px-4 py-6 text-center text-[13px] text-ink-3">
              Nothing matches “{query}”.
            </li>
          )}
          {matches.map((command, index) => (
            <li
              key={command.id}
              id={`command-${command.id}`}
              role="option"
              aria-selected={index === current}
              data-index={index}
              onMouseMove={() => setActive(index)}
              onClick={() => go(command)}
              className={cx(
                "flex cursor-pointer items-center gap-3 px-4 py-2 text-[13px]",
                index === current ? "bg-selected text-ink" : "text-ink-2",
              )}
            >
              <span className="w-20 shrink-0 text-[11.5px] text-ink-3">
                {command.group}
              </span>
              <span className="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap">
                {command.label}
              </span>
              {index === current && (
                <CornerDownLeft
                  aria-hidden
                  className="size-3.5 shrink-0 text-ink-3"
                />
              )}
            </li>
          ))}
        </ul>
      </div>
    </Dialog>
  );
}

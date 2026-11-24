import type { Portfolio } from "../api";
import { timeIst, shortDate } from "../format";
import { cx } from "../components/ui";
import { DeskEmpty, DeskPanel, DeskSkeleton } from "./DeskPanel";

// Colour only for what changes money: fills and exits.
function tone(kind: string): string {
  if (/fill|open|enter/i.test(kind)) return "text-up";
  if (/stop|loss|halt|reject|fail/i.test(kind)) return "text-down";
  if (/exit|close|target|settle/i.test(kind)) return "text-accent-ink";
  return "text-ink-3";
}

/** Everything the paper desk did, newest first, as a terminal log. */
export function EventLog({ portfolio }: { portfolio: Portfolio | undefined }) {
  const events = portfolio?.activity ?? [];
  return (
    <DeskPanel
      title="Fills and events"
      aside={events.length > 0 && <span>Last {events.length}</span>}
    >
      {portfolio === undefined ? (
        <DeskSkeleton rows={4} />
      ) : events.length === 0 ? (
        <DeskEmpty>Nothing has happened on the paper desk yet.</DeskEmpty>
      ) : (
        <ol
          aria-label="Events, newest first"
          className="m-0 list-none py-1 pl-0 font-mono text-[12px] leading-relaxed"
        >
          {events.map((event, index) => (
            <li
              key={`${event.ts}-${event.kind}-${event.position_id ?? ""}-${event.detail}`}
              className={cx(
                "grid grid-cols-[4.5rem_6.5rem_minmax(0,1fr)] gap-3 px-3 hover:bg-hover",
                index === 0 &&
                  "animate-[row-in_150ms_ease-out] motion-reduce:animate-none",
              )}
            >
              <time
                dateTime={event.ts}
                className="text-ink-3"
                title={shortDate(event.ts)}
              >
                {timeIst(event.ts)}
              </time>
              <span
                className={cx(
                  "overflow-hidden text-ellipsis whitespace-nowrap",
                  tone(event.kind),
                )}
              >
                {event.kind.replaceAll("_", " ")}
              </span>
              <span className="overflow-hidden text-ellipsis whitespace-nowrap text-ink-2">
                {event.position_id ? (
                  <span className="text-ink-3">#{event.position_id} </span>
                ) : null}
                {event.detail.replaceAll("_", " ")}
              </span>
            </li>
          ))}
        </ol>
      )}
    </DeskPanel>
  );
}

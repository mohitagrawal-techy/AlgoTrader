import { ChevronLeft, ChevronRight, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type CalendarItem,
  type CalendarKind,
  type CalendarPayload,
} from "../../api";
import {
  Alert,
  Button,
  ChipCheck,
  cx,
  Empty,
  Field,
  GroupLabel,
  Panel,
  Segmented,
  Status,
  TextInput,
} from "../ui";

const KINDS: Record<CalendarKind, { label: string; mark: string }> = {
  owner: { label: "Your events", mark: "bg-accent" },
  config: { label: "events.yaml", mark: "bg-accent" },
  ai: { label: "Found by the AI brief", mark: "bg-warning" },
  holiday: { label: "Market holidays", mark: "bg-down" },
  special: { label: "Special sessions", mark: "bg-up" },
  expiry: { label: "Option expiries", mark: "bg-ink-3" },
};
const FILTERS: CalendarKind[] = ["owner", "ai", "holiday", "expiry"];
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const iso = (d: Date) => d.toISOString().slice(0, 10);
const utc = (y: number, m: number, d: number) => new Date(Date.UTC(y, m, d));
const longDate = (value: string) =>
  new Date(`${value}T00:00:00Z`).toLocaleDateString("en-IN", {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });

/** Monday-first weeks covering the month, as ISO dates. */
function monthGrid(year: number, month: number): string[] {
  const first = utc(year, month, 1);
  const offset = (first.getUTCDay() + 6) % 7;
  const start = utc(year, month, 1 - offset);
  const last = utc(year, month + 1, 0);
  const days = Math.ceil((offset + last.getUTCDate()) / 7) * 7;
  return Array.from({ length: days }, (_, i) =>
    iso(
      utc(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate() + i),
    ),
  );
}

function visible(item: CalendarItem, shown: Set<CalendarKind>): boolean {
  if (item.kind === "config") return shown.has("owner");
  if (item.kind === "special") return shown.has("holiday");
  return shown.has(item.kind);
}

function ItemLine({
  item,
  onDelete,
}: {
  item: CalendarItem;
  onDelete?: (id: string) => void;
}) {
  return (
    <li className="flex min-w-0 items-start gap-2.5 py-1.5">
      <span
        aria-hidden
        className={cx(
          "mt-1.5 size-2 shrink-0 rounded-full",
          KINDS[item.kind].mark,
        )}
      />
      <span className="min-w-0 flex-1">
        <span className="text-[13px] text-ink">{item.name}</span>
        {item.severity === "high" && (
          <span className="ml-2">
            <Status tone="critical">High impact</Status>
          </span>
        )}
        <span className="block text-[11.5px] text-ink-3">
          {KINDS[item.kind].label}
          {item.symbols.length ? ` · ${item.symbols.join(", ")}` : ""}
          {item.note ? ` · ${item.note}` : ""}
        </span>
      </span>
      {item.kind === "owner" && item.id && onDelete && (
        <Button
          variant="ghost"
          size="icon"
          icon={Trash2}
          aria-label={`Delete ${item.name}`}
          title="Delete this event"
          onClick={() => onDelete(item.id as string)}
        />
      )}
    </li>
  );
}

function AddEvent({
  symbols,
  initialDate,
  onAdded,
}: {
  symbols: string[];
  initialDate: string;
  onAdded: () => void;
}) {
  const [date, setDate] = useState(initialDate);
  const [name, setName] = useState("");
  const [severity, setSeverity] = useState<"high" | "medium">("high");
  const [chosen, setChosen] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setDate(initialDate), [initialDate]);

  const submit = () => {
    if (!name.trim() || !date) {
      setError("Give the event a date and a name.");
      return;
    }
    setBusy(true);
    setError(null);
    api
      .calendarAdd({
        date,
        name: name.trim(),
        severity,
        symbols: chosen,
        note: note.trim(),
      })
      .then(() => {
        setName("");
        setNote("");
        setChosen([]);
        onAdded();
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  return (
    <form
      className="flex flex-col gap-3 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <div className="grid gap-3 sm:grid-cols-[10rem_1fr]">
        <Field id="event-date" label="Date">
          <input
            id="event-date"
            type="date"
            value={date}
            onChange={(event) => setDate(event.target.value)}
            className="h-9 rounded-md border border-line bg-surface px-2.5 text-[13.5px] text-ink"
          />
        </Field>
        <Field id="event-name" label="Event">
          <TextInput
            id="event-name"
            value={name}
            placeholder="RBI policy decision"
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
      </div>
      <div className="flex flex-wrap items-end gap-4">
        <div className="flex flex-col gap-1.5">
          <GroupLabel id="event-severity">Impact</GroupLabel>
          <Segmented
            label="Impact"
            value={severity}
            options={[
              { value: "high", label: "High" },
              { value: "medium", label: "Medium" },
            ]}
            onChange={setSeverity}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <GroupLabel id="event-symbols">
            Underlyings (none means all)
          </GroupLabel>
          <div
            role="group"
            aria-labelledby="event-symbols"
            className="flex flex-wrap gap-1.5"
          >
            {symbols.map((symbol) => (
              <ChipCheck
                key={symbol}
                checked={chosen.includes(symbol)}
                onChange={(on) =>
                  setChosen((current) =>
                    on
                      ? [...current, symbol]
                      : current.filter((s) => s !== symbol),
                  )
                }
              >
                {symbol}
              </ChipCheck>
            ))}
          </div>
        </div>
      </div>
      <Field id="event-note" label="Note (optional)">
        <TextInput
          id="event-note"
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </Field>
      {error && <Alert tone="critical">{error}</Alert>}
      <div>
        <Button type="submit" variant="primary" icon={Plus} disabled={busy}>
          Add event
        </Button>
      </div>
      <p className="m-0 text-[12px] text-ink-3">
        High-impact events block short-premium ideas whose holding period spans
        them. Medium ones lower an idea&apos;s event score.
      </p>
    </form>
  );
}

function Reviewed({
  data,
  suggested,
  onChanged,
}: {
  data: CalendarPayload;
  suggested: string;
  onChanged: () => void;
}) {
  const [through, setThrough] = useState(suggested);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setThrough(suggested), [suggested]);
  const reviewed = data.reviewed_through;
  const save = (value: string | null) => {
    setError(null);
    api
      .calendarReviewed(value)
      .then(onChanged)
      .catch((err: Error) => setError(err.message));
  };
  const ahead = reviewed
    ? (Date.parse(reviewed) - Date.parse(data.today)) / 86400000
    : null;
  return (
    <div className="flex flex-col gap-3 p-3">
      {ahead === null || ahead < 7 ? (
        <Alert tone="warning" title="Short-premium ideas are blocked">
          {reviewed
            ? `The calendar is reviewed only through ${longDate(reviewed)}. A short-premium idea held past that date is rejected, because an unchecked calendar must not read as no events.`
            : "The calendar has never been marked reviewed, so every short-premium idea is rejected: an unchecked calendar must not read as no events."}
        </Alert>
      ) : (
        <p className="m-0 text-[13px] text-ink">
          Reviewed through <b>{longDate(reviewed as string)}</b>. Short-premium
          ideas may be held up to then, outside high-impact events.
        </p>
      )}
      <div className="flex flex-wrap items-end gap-3">
        <Field id="reviewed-through" label="I have checked every event through">
          <input
            id="reviewed-through"
            type="date"
            value={through}
            onChange={(event) => setThrough(event.target.value)}
            className="h-9 rounded-md border border-line bg-surface px-2.5 text-[13.5px] text-ink"
          />
        </Field>
        <Button
          variant="primary"
          disabled={!through}
          onClick={() => save(through)}
        >
          Mark reviewed
        </Button>
        {data.reviewed_sources.dashboard && (
          <Button variant="ghost" onClick={() => save(null)}>
            Clear the dashboard date
          </Button>
        )}
      </div>
      {data.reviewed_sources.config && (
        <p className="m-0 text-[12px] text-ink-3">
          events.yaml also sets {longDate(data.reviewed_sources.config)}; the
          later of the two applies.
        </p>
      )}
      {error && <Alert tone="critical">{error}</Alert>}
    </div>
  );
}

/** A month of events, holidays and expiries, with your own events and the review date. */
export function CalendarPage() {
  const today = new Date();
  const [cursor, setCursor] = useState({
    year: today.getFullYear(),
    month: today.getMonth(),
  });
  const [data, setData] = useState<CalendarPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<Set<CalendarKind>>(new Set(FILTERS));
  const [picked, setPicked] = useState<string | null>(null);

  const days = useMemo(() => monthGrid(cursor.year, cursor.month), [cursor]);
  const load = useCallback(() => {
    api
      .calendar(days[0], days[days.length - 1])
      .then((found) => {
        setData(found);
        setError(null);
      })
      .catch((err: Error) => setError(err.message));
  }, [days]);
  useEffect(load, [load]);

  const byDay = useMemo(() => {
    const map = new Map<string, CalendarItem[]>();
    for (const item of data?.items ?? []) {
      if (!visible(item, shown)) continue;
      map.set(item.date, [...(map.get(item.date) ?? []), item]);
    }
    return map;
  }, [data, shown]);

  if (error) return <Alert tone="critical">{error}</Alert>;
  if (!data) return <Empty title="Loading…" />;

  const monthKey = `${cursor.year}-${String(cursor.month + 1).padStart(2, "0")}`;
  const title = utc(cursor.year, cursor.month, 1).toLocaleDateString("en-IN", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
  const agendaDays = [...byDay.keys()]
    .filter((day) => day.startsWith(monthKey))
    .sort();
  const move = (step: number) =>
    setCursor(({ year, month }) => {
      const next = utc(year, month + step, 1);
      return { year: next.getUTCFullYear(), month: next.getUTCMonth() };
    });
  const remove = (id: string) =>
    api
      .calendarDelete(id)
      .then(load)
      .catch((err: Error) => setError(err.message));
  const monthEnd = iso(utc(cursor.year, cursor.month + 1, 0));

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="icon"
          icon={ChevronLeft}
          aria-label="Previous month"
          onClick={() => move(-1)}
        />
        <h2 className="m-0 min-w-36 text-center text-[15px] font-semibold text-ink">
          {title}
        </h2>
        <Button
          size="icon"
          icon={ChevronRight}
          aria-label="Next month"
          onClick={() => move(1)}
        />
        <Button
          onClick={() =>
            setCursor({ year: today.getFullYear(), month: today.getMonth() })
          }
        >
          Today
        </Button>
        <div
          role="group"
          aria-label="Show"
          className="ml-auto flex flex-wrap gap-1.5"
        >
          {FILTERS.map((kind) => (
            <ChipCheck
              key={kind}
              checked={shown.has(kind)}
              onChange={(on) =>
                setShown((current) => {
                  const next = new Set(current);
                  if (on) next.add(kind);
                  else next.delete(kind);
                  return next;
                })
              }
            >
              <span
                aria-hidden
                className={cx(
                  "mr-1.5 inline-block size-2 rounded-full",
                  KINDS[kind].mark,
                )}
              />
              {KINDS[kind].label}
            </ChipCheck>
          ))}
        </div>
      </div>

      <div className="hidden overflow-hidden rounded-md border border-line sm:block">
        <div className="grid grid-cols-7 border-b border-line bg-surface-2">
          {WEEKDAYS.map((day) => (
            <div
              key={day}
              className="px-2 py-1.5 text-[11.5px] font-medium text-ink-3"
            >
              {day}
            </div>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-px bg-line">
          {days.map((day) => {
            const items = byDay.get(day) ?? [];
            const inMonth = day.startsWith(monthKey);
            const closed = items.some((item) => item.kind === "holiday");
            const weekend = [5, 6].includes(
              (new Date(`${day}T00:00:00Z`).getUTCDay() + 6) % 7,
            );
            return (
              <button
                key={day}
                type="button"
                onClick={() => setPicked(day)}
                aria-label={`${longDate(day)}: ${items.length} items`}
                className={cx(
                  "flex min-h-24 min-w-0 cursor-pointer flex-col gap-1 border-0 bg-surface p-1.5 text-left align-top hover:bg-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent",
                  !inMonth && "opacity-45",
                  (weekend || closed) && "bg-surface-2",
                  day === picked &&
                    "outline outline-1 -outline-offset-1 outline-accent",
                )}
              >
                <span
                  className={cx(
                    "font-mono text-[11.5px] tabular-nums",
                    day === data.today
                      ? "w-fit rounded-sm bg-accent px-1 font-semibold text-white"
                      : "text-ink-3",
                  )}
                >
                  {Number(day.slice(8))}
                </span>
                {items.slice(0, 3).map((item, index) => (
                  <span
                    key={index}
                    title={item.name}
                    className="flex min-w-0 items-center gap-1 text-[11.5px] leading-tight text-ink-2"
                  >
                    <span
                      aria-hidden
                      className={cx(
                        "size-1.5 shrink-0 rounded-full",
                        KINDS[item.kind].mark,
                      )}
                    />
                    <span className="truncate">{item.name}</span>
                  </span>
                ))}
                {items.length > 3 && (
                  <span className="text-[11px] text-ink-3">
                    +{items.length - 3} more
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      <div className="grid gap-3 xl:grid-cols-12">
        <div className="flex min-w-0 flex-col xl:col-span-7">
          <Panel
            title={picked ? longDate(picked) : `This month`}
            aside={
              picked && (
                <Button variant="link" onClick={() => setPicked(null)}>
                  Show the whole month
                </Button>
              )
            }
          >
            <div className="px-3 py-2">
              {(picked ? [picked] : agendaDays).map((day) => {
                const items = byDay.get(day) ?? [];
                return (
                  <section
                    key={day}
                    className="border-b border-grid py-1.5 last:border-b-0"
                  >
                    {!picked && (
                      <h3 className="m-0 text-[12px] font-semibold text-ink-2">
                        {longDate(day)}
                      </h3>
                    )}
                    {items.length ? (
                      <ul className="m-0 list-none pl-0">
                        {items.map((item, index) => (
                          <ItemLine key={index} item={item} onDelete={remove} />
                        ))}
                      </ul>
                    ) : (
                      <p className="m-0 py-1.5 text-[13px] text-ink-3">
                        Nothing on this day.
                      </p>
                    )}
                  </section>
                );
              })}
              {!picked && agendaDays.length === 0 && (
                <p className="m-0 py-2 text-[13px] text-ink-3">
                  Nothing this month for the kinds shown.
                </p>
              )}
            </div>
          </Panel>
        </div>
        <div className="flex min-w-0 flex-col gap-3 xl:col-span-5">
          <Panel title="Review">
            <Reviewed data={data} suggested={monthEnd} onChanged={load} />
          </Panel>
          <Panel title="Add an event">
            <AddEvent
              symbols={data.symbols}
              initialDate={picked ?? data.today}
              onAdded={load}
            />
          </Panel>
        </div>
      </div>
    </div>
  );
}

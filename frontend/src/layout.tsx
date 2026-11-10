import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
} from "react";

const COLUMNS = 12;
const GAP = 14;
const MIN_SPAN = 3;
const MIN_HEIGHT = 160;
const MAX_HEIGHT = 1400;
const HEIGHT_STEP = 8;
const KEY_HEIGHT_STEP = 40;
const SCROLL_EDGE = 72;
const SCROLL_STEP = 14;

export interface CardSize {
  span?: number;
  height?: number;
  order?: number;
}

export type GridLayout = Record<string, CardSize>;

export interface Card {
  id: string;
  title: string;
  span: number;
  node: ReactNode;
}

function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value));
}

function readLayout(key: string): GridLayout {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) ?? "{}");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function writeLayout(key: string, layout: GridLayout): void {
  try {
    if (Object.keys(layout).length) localStorage.setItem(key, JSON.stringify(layout));
    else localStorage.removeItem(key);
  } catch {
    // Without storage the layout still works until the page is reloaded.
  }
}

/** A saved panel layout for this browser. */
export function useGridLayout(storageKey: string) {
  const [layout, setLayout] = useState<GridLayout>(() => readLayout(storageKey));
  const save = useCallback((next: GridLayout) => writeLayout(storageKey, next), [storageKey]);
  const reset = useCallback(() => {
    setLayout({});
    writeLayout(storageKey, {});
  }, [storageKey]);
  return { layout, setLayout, save, reset, customised: Object.keys(layout).length > 0 };
}

function spanOf(card: Card, layout: GridLayout): number {
  return clamp(Math.round(layout[card.id]?.span ?? card.span), MIN_SPAN, COLUMNS);
}

/** Cards in the saved order. A card with no saved place stays at its default position. */
function orderedCards(cards: Card[], layout: GridLayout): Card[] {
  return cards
    .map((card, index) => ({ card, place: layout[card.id]?.order ?? index }))
    .sort((a, b) => a.place - b.place)
    .map((item) => item.card);
}

/**
 * Swaps two cards. Width and height stay with the position, so the rows stay full and no other
 * panel moves.
 */
function withSwap(cards: Card[], layout: GridLayout, first: string, second: string): GridLayout {
  const ordered = orderedCards(cards, layout);
  const place = (id: string) => ordered.findIndex((card) => card.id === id);
  const a = ordered[place(first)];
  const b = ordered[place(second)];
  const next: GridLayout = {};
  ordered.forEach((card, index) => {
    next[card.id] = { ...layout[card.id], order: index };
  });
  next[first] = { span: spanOf(b, layout), height: layout[second]?.height, order: place(second) };
  next[second] = { span: spanOf(a, layout), height: layout[first]?.height, order: place(first) };
  return next;
}

/** Groups cards the way the grid places them: left to right, wrapping when a row is full. */
function rowsOf(cards: Card[], layout: GridLayout): Card[][] {
  const rows: Card[][] = [];
  let used = COLUMNS;
  for (const card of cards) {
    const span = spanOf(card, layout);
    if (used + span > COLUMNS) {
      rows.push([]);
      used = 0;
    }
    rows[rows.length - 1].push(card);
    used += span;
  }
  return rows;
}

/** Sets a card's width. A card to its right gives up or takes back the same columns. */
function withSpan(cards: Card[], layout: GridLayout, id: string, target: number): GridLayout {
  const row = rowsOf(cards, layout).find((items) => items.some((card) => card.id === id));
  if (!row) return layout;
  const index = row.findIndex((card) => card.id === id);
  const current = spanOf(row[index], layout);
  const neighbour = row[index + 1];
  if (neighbour) {
    const shared = current + spanOf(neighbour, layout);
    const span = clamp(target, MIN_SPAN, shared - MIN_SPAN);
    return {
      ...layout,
      [id]: { ...layout[id], span },
      [neighbour.id]: { ...layout[neighbour.id], span: shared - span },
    };
  }
  const others = row.reduce((sum, card) => sum + (card.id === id ? 0 : spanOf(card, layout)), 0);
  return { ...layout, [id]: { ...layout[id], span: clamp(target, MIN_SPAN, COLUMNS - others) } };
}

/** Sets the height of a whole row, so the cards in it stay level. */
function withHeight(cards: Card[], layout: GridLayout, id: string, target: number): GridLayout {
  const row = rowsOf(cards, layout).find((items) => items.some((card) => card.id === id));
  if (!row) return layout;
  const height = clamp(Math.round(target / HEIGHT_STEP) * HEIGHT_STEP, MIN_HEIGHT, MAX_HEIGHT);
  const next = { ...layout };
  for (const card of row) next[card.id] = { ...next[card.id], height };
  return next;
}

interface Drag {
  id: string;
  x: boolean;
  y: boolean;
  startX: number;
  startY: number;
  width: number;
  height: number;
  unit: number;
}

interface Moving {
  id: string;
  dx: number;
  dy: number;
  target: string | null;
}

export function ResizableGrid({
  cards: unordered,
  layout,
  arranging,
  onChange,
  onCommit,
}: {
  cards: Card[];
  layout: GridLayout;
  arranging: boolean;
  onChange: (next: GridLayout) => void;
  onCommit: (next: GridLayout) => void;
}) {
  const grid = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const latest = useRef(layout);
  latest.current = layout;
  const [activeId, setActiveId] = useState<string | null>(null);
  const cards = orderedCards(unordered, layout);
  const [moving, setMoving] = useState<Moving | null>(null);
  const carry = useRef<{
    id: string;
    startX: number;
    startY: number;
    startScroll: number;
    x: number;
    y: number;
    timer: number;
  } | null>(null);
  const targetId = useRef<string | null>(null);
  const refocus = useRef<{ id: string; direction: "earlier" | "later" } | null>(null);

  useEffect(() => () => window.clearInterval(carry.current?.timer), []);

  // Reordering moves the card in the page, which drops keyboard focus from its button.
  useLayoutEffect(() => {
    const wanted = refocus.current;
    if (!wanted || !grid.current) return;
    refocus.current = null;
    const overlay = grid.current.querySelector(`[data-card-id="${wanted.id}"]`);
    const button =
      overlay?.querySelector<HTMLButtonElement>(`.arrange-${wanted.direction}:not(:disabled)`) ??
      overlay?.querySelector<HTMLButtonElement>("button:not(:disabled)");
    button?.focus();
  });

  const trackCarry = () => {
    const active = carry.current;
    if (!active) return;
    const over = document
      .elementsFromPoint(active.x, active.y)
      .map((element) => element.closest<HTMLElement>("[data-card-id]")?.dataset.cardId)
      .find((id) => id && id !== active.id);
    targetId.current = over ?? null;
    setMoving({
      id: active.id,
      dx: active.x - active.startX,
      dy: active.y - active.startY + window.scrollY - active.startScroll,
      target: targetId.current,
    });
  };

  const beginCarry = (event: PointerEvent<HTMLElement>, id: string) => {
    // On a touch screen this would block scrolling, so the move buttons are used instead.
    if (event.pointerType === "touch" || event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const timer = window.setInterval(() => {
      const active = carry.current;
      if (!active) return;
      if (active.y < SCROLL_EDGE) window.scrollBy(0, -SCROLL_STEP);
      else if (active.y > window.innerHeight - SCROLL_EDGE) window.scrollBy(0, SCROLL_STEP);
      else return;
      trackCarry();
    }, 16);
    carry.current = {
      id,
      startX: event.clientX,
      startY: event.clientY,
      startScroll: window.scrollY,
      x: event.clientX,
      y: event.clientY,
      timer,
    };
    trackCarry();
  };

  const moveCarry = (event: PointerEvent<HTMLElement>) => {
    if (!carry.current) return;
    carry.current.x = event.clientX;
    carry.current.y = event.clientY;
    trackCarry();
  };

  const endCarry = (apply: boolean) => {
    const active = carry.current;
    if (!active) return;
    window.clearInterval(active.timer);
    carry.current = null;
    setMoving(null);
    if (apply && targetId.current) {
      const next = withSwap(cards, latest.current, active.id, targetId.current);
      onChange(next);
      onCommit(next);
    }
    targetId.current = null;
  };

  const step = (id: string, offset: number) => {
    const index = cards.findIndex((card) => card.id === id);
    const other = cards[index + offset];
    if (!other) return;
    refocus.current = { id, direction: offset < 0 ? "earlier" : "later" };
    const next = withSwap(cards, latest.current, id, other.id);
    onChange(next);
    onCommit(next);
  };

  const begin = (event: PointerEvent<HTMLElement>, id: string, x: boolean, y: boolean) => {
    const card = event.currentTarget.parentElement;
    if (!card || !grid.current) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const bounds = card.getBoundingClientRect();
    drag.current = {
      id,
      x,
      y,
      startX: event.clientX,
      startY: event.clientY,
      width: bounds.width,
      height: bounds.height,
      unit: (grid.current.clientWidth + GAP) / COLUMNS,
    };
    setActiveId(id);
  };

  const move = (event: PointerEvent<HTMLElement>) => {
    const active = drag.current;
    if (!active) return;
    let next = latest.current;
    if (active.x) {
      const target = Math.round((active.width + event.clientX - active.startX + GAP) / active.unit);
      if (target !== spanOf(cards.find((c) => c.id === active.id) as Card, next)) {
        next = withSpan(cards, next, active.id, target);
      }
    }
    if (active.y) {
      next = withHeight(cards, next, active.id, active.height + event.clientY - active.startY);
    }
    if (next !== latest.current) {
      latest.current = next;
      onChange(next);
    }
  };

  const end = () => {
    if (!drag.current) return;
    drag.current = null;
    setActiveId(null);
    onCommit(latest.current);
  };

  const onKey = (event: KeyboardEvent<HTMLButtonElement>, card: Card) => {
    const steps: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -KEY_HEIGHT_STEP],
      ArrowDown: [0, KEY_HEIGHT_STEP],
    };
    const step = steps[event.key];
    if (!step) return;
    event.preventDefault();
    let next = latest.current;
    if (step[0]) next = withSpan(cards, next, card.id, spanOf(card, next) + step[0]);
    if (step[1]) {
      const height = (event.currentTarget.parentElement as HTMLElement).getBoundingClientRect().height;
      next = withHeight(cards, next, card.id, height + step[1]);
    }
    onChange(next);
    onCommit(next);
  };

  const rows = rowsOf(cards, layout);
  const heights = new Map<string, number | undefined>();
  for (const row of rows) {
    const chosen = row.map((card) => layout[card.id]?.height).filter((h): h is number => !!h);
    const height = chosen.length ? clamp(Math.max(...chosen), MIN_HEIGHT, MAX_HEIGHT) : undefined;
    for (const card of row) heights.set(card.id, height);
  }

  return (
    <div className="dash-grid" ref={grid}>
      {cards.map((card, index) => {
        const height = heights.get(card.id);
        const style = {
          "--span": spanOf(card, layout),
          ...(height ? { "--card-height": `${height}px` } : {}),
          ...(moving?.id === card.id
            ? { transform: `translate(${moving.dx}px, ${moving.dy}px)` }
            : {}),
        } as CSSProperties;
        const classes = [
          "dash-card",
          card.id === activeId && "resizing",
          moving?.id === card.id && "moving",
          moving?.target === card.id && "swap-target",
        ]
          .filter(Boolean)
          .join(" ");
        if (arranging) {
          return (
            <div className={classes} style={style} key={card.id}>
              {card.node}
              <div
                className="arrange-overlay"
                data-card-id={card.id}
                onPointerDown={(event) => beginCarry(event, card.id)}
                onPointerMove={moveCarry}
                onPointerUp={() => endCarry(true)}
                onPointerCancel={() => endCarry(false)}
              >
                <div className="arrange-bar">
                  <span className="arrange-title">{card.title}</span>
                  <span className="arrange-buttons" onPointerDown={(event) => event.stopPropagation()}>
                    <button
                      type="button"
                      className="button ghost arrange-earlier"
                      aria-label={`Move ${card.title} earlier`}
                      title="Move earlier"
                      disabled={index === 0}
                      onClick={() => step(card.id, -1)}
                    >
                      ←
                    </button>
                    <button
                      type="button"
                      className="button ghost arrange-later"
                      aria-label={`Move ${card.title} later`}
                      title="Move later"
                      disabled={index === cards.length - 1}
                      onClick={() => step(card.id, 1)}
                    >
                      →
                    </button>
                  </span>
                </div>
              </div>
            </div>
          );
        }
        return (
          <div className={classes} style={style} key={card.id}>
            {card.node}
            {[
              ["resize-east", true, false],
              ["resize-south", false, true],
            ].map(([name, x, y]) => (
              <div
                key={name as string}
                className={name as string}
                aria-hidden
                onPointerDown={(event) => begin(event, card.id, x as boolean, y as boolean)}
                onPointerMove={move}
                onPointerUp={end}
                onPointerCancel={end}
              />
            ))}
            <button
              type="button"
              className="resize-corner"
              aria-label={`Resize ${card.title}. Left and right arrows change width, up and down change height.`}
              title="Drag to resize"
              onPointerDown={(event) => begin(event, card.id, true, true)}
              onPointerMove={move}
              onPointerUp={end}
              onPointerCancel={end}
              onKeyDown={(event) => onKey(event, card)}
            />
          </div>
        );
      })}
    </div>
  );
}

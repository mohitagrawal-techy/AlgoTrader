import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

/** How one feature works, with a worked example on one trade. */
export type FeatureHelpContent = {
  how: string;
  /** The example's starting settings, shown as chips. */
  setup: string[];
  head: string[];
  rows: string[][];
  /** Rows to stand out, such as the exit. */
  highlight?: number[];
  after: string;
  note?: string;
};

// The examples buy one lot of a Nifty call (65 units), so 1 point of option price is ₹65.
// They follow the engine: stops act on the trade's P&L, which for one bought option moves
// point for point with its price.
export const FEATURE_HELP: Record<
  "trail" | "breakeven" | "reenter" | "reexecute" | "wait",
  FeatureHelpContent
> = {
  trail: {
    how: "Once the trade is in profit, the stop loss follows the highest price reached and stays the trail distance below it. It only ever moves up.",
    setup: [
      "Buy 1 lot Nifty CE at ₹100",
      "Stop loss 20 pts, at ₹80",
      "Trail 10 pts",
    ],
    head: ["Option price", "Gain", "Stop loss now at"],
    rows: [
      ["₹100", "0", "₹80, the original stop"],
      ["₹105", "+5 pts", "₹95"],
      ["₹110", "+10 pts", "₹100, the entry"],
      ["₹125", "+25 pts", "₹115"],
      ["₹130", "+30 pts", "₹120"],
      ["Falls to ₹120", "+20 pts", "Stop hit, the trade exits"],
    ],
    highlight: [5],
    after:
      "Result: +20 pts × 65 = +₹1,300. Without the trail the stop stays at ₹80, so the price could fall all the way there: a loss of ₹1,300.",
    note: "For a sold option it works the other way round: profit comes as the price falls, so the stop follows the lowest price, staying the distance above it.",
  },
  breakeven: {
    how: "Once the gain reaches the set amount, the stop loss jumps to the entry price. From then on the trade can end at no loss, but no worse.",
    setup: [
      "Buy 1 lot Nifty CE at ₹100",
      "Stop loss 20 pts, at ₹80",
      "Move to cost after 15 pts",
    ],
    head: ["Option price", "Gain", "Stop loss now at"],
    rows: [
      ["₹108", "+8 pts", "₹80, not moved yet"],
      ["₹115", "+15 pts", "₹100, moved to the entry"],
      ["₹125", "+25 pts", "₹100, stays there"],
      ["Falls to ₹100", "0", "Stop hit, the trade exits"],
    ],
    highlight: [1, 3],
    after:
      "Result: about ₹0, only charges. Without it, the trade would have stayed open down to ₹80 and lost ₹1,300.",
    note: "Unlike a trailing stop it moves once, to the entry, and no further. With both on, the higher stop applies.",
  },
  reenter: {
    how: "After a stop loss closes a trade, the strategy keeps watching. If its entry rules hold again the same day, it takes a new trade. Turned off, the first stop ends trading for that day.",
    setup: [
      "Entry: 15-min close above EMA 20",
      "Buy CE, stop loss 20 pts",
      "Max 3 trades a day",
    ],
    head: ["Time", "What happens"],
    rows: [
      ["09:45", "Close above EMA 20, buys CE at ₹100"],
      ["10:30", "CE falls to ₹80, stop hit: −₹1,300"],
      ["11:15", "Close above EMA 20 again"],
      ["If on", "Buys CE again at ₹95, with a new stop at ₹75"],
      ["If off", "No trade. Done for the day"],
    ],
    highlight: [3, 4],
    after:
      "A trailing stop, a move-to-cost exit and a leg's own stop all count as a stop here.",
    note: "Re-entries still count towards the day's trade limit and loss limit.",
  },
  reexecute: {
    how: "After a profit target closes a trade, the strategy keeps watching. If its entry rules hold again the same day, it takes a new trade. Turned off, the first target ends trading for that day.",
    setup: [
      "Entry: 15-min close above EMA 20",
      "Buy CE, target 30 pts",
      "Max 3 trades a day",
    ],
    head: ["Time", "What happens"],
    rows: [
      ["09:45", "Close above EMA 20, buys CE at ₹100"],
      ["10:40", "CE reaches ₹130, target hit: +₹1,950"],
      ["11:30", "Close above EMA 20 again"],
      ["If on", "Buys CE again at ₹118, with a new target at ₹148"],
      ["If off", "No trade. The day's profit is kept"],
    ],
    highlight: [3, 4],
    after: "A leg's own target counts as a target here too.",
    note: "New trades still count towards the day's trade limit and profit target.",
  },
  wait: {
    how: "When the entry rules hold, the strategy does not buy straight away. It notes the option's price and buys only once that price moves by the set amount, checking at each decision time.",
    setup: ["Rules hold at 10:00", "ATM CE at ₹100", "Wait for: up 10%"],
    head: ["Time", "CE price", "What happens"],
    rows: [
      ["10:00", "₹100", "Rules hold. Waits for ₹110"],
      ["10:15", "₹104", "Still waiting"],
      ["10:30", "₹97", "Still waiting"],
      ["10:45", "₹111", "Reached ₹110, buys at ₹111"],
    ],
    highlight: [3],
    after:
      "Without wait and trade it would have bought at ₹100 at 10:00. If the CE never reaches ₹110 before the close, there is no trade that day.",
    note: "Down works the other way: with down 10%, it buys only once the price falls to ₹90. It watches the same contract it picked at 10:00. For a spread, the price is the net premium of all its legs.",
  },
};

const WIDTH = 420;
const GAP = 8;

/** An info button beside a feature's name. Hovering or focusing it shows how the feature
 * works; clicking keeps the card open until Escape or a click elsewhere. */
export function FeatureHelp({
  title,
  help,
}: {
  title: string;
  help: FeatureHelpContent;
}) {
  const id = useId();
  const button = useRef<HTMLButtonElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<{
    top: number;
    up: boolean;
    left: number;
    maxHeight: number;
  } | null>(null);
  const [pinned, setPinned] = useState(false);

  const show = () => {
    const box = button.current?.getBoundingClientRect();
    if (!box) return;
    const width = Math.min(WIDTH, window.innerWidth - 16);
    const left = Math.min(
      Math.max(8, box.left - 16),
      window.innerWidth - width - 8,
    );
    const below = window.innerHeight - box.bottom - GAP - 8;
    const above = box.top - GAP - 8;
    // Below the button unless there is clearly more room above it, where it shifts up by its own height.
    if (below >= 320 || below >= above)
      setPlace({ top: box.bottom + GAP, up: false, left, maxHeight: below });
    else setPlace({ top: box.top - GAP, up: true, left, maxHeight: above });
  };
  const hide = () => {
    setPlace(null);
    setPinned(false);
  };

  useEffect(() => {
    if (!place) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        hide();
        button.current?.focus();
      }
    };
    const onDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!button.current?.contains(target) && !card.current?.contains(target))
        hide();
    };
    const onScroll = (event: Event) => {
      if (!card.current?.contains(event.target as Node)) hide();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [!!place]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <button
        ref={button}
        type="button"
        className="feature-help-button"
        aria-label={`How ${title.toLowerCase()} works`}
        aria-expanded={!!place}
        aria-controls={place ? id : undefined}
        onMouseEnter={() => !pinned && show()}
        onMouseLeave={() => !pinned && setPlace(null)}
        onFocus={() => !pinned && show()}
        onBlur={() => !pinned && setPlace(null)}
        onClick={() => {
          if (pinned) hide();
          else {
            show();
            setPinned(true);
          }
        }}
      >
        <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
          <circle
            cx="8"
            cy="8"
            r="7"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.4"
          />
          <circle cx="8" cy="4.8" r="0.95" fill="currentColor" />
          <path
            d="M8 7.2v4.6"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
        </svg>
      </button>
      {place &&
        createPortal(
          <div
            id={id}
            ref={card}
            role={pinned ? "dialog" : "tooltip"}
            aria-label={pinned ? title : undefined}
            className="feature-help"
            style={{
              top: place.top,
              transform: place.up ? "translateY(-100%)" : undefined,
              left: place.left,
              width: Math.min(WIDTH, window.innerWidth - 16),
              maxHeight: place.maxHeight,
            }}
          >
            <div className="feature-help-title">{title}</div>
            <div className="feature-help-body">
              <h4>How it works</h4>
              <p>{help.how}</p>
              <h4>Example</h4>
              <div className="feature-help-setup">
                {help.setup.map((item) => (
                  <span key={item}>{item}</span>
                ))}
              </div>
              <table>
                <thead>
                  <tr>
                    {help.head.map((cell) => (
                      <th key={cell} scope="col">
                        {cell}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {help.rows.map((row, rowIndex) => (
                    <tr
                      key={row.join("|")}
                      className={
                        help.highlight?.includes(rowIndex)
                          ? "is-key"
                          : undefined
                      }
                    >
                      {row.map((cell, index) => (
                        <td key={index}>{cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              <p>{help.after}</p>
              {help.note && <p className="feature-help-note">{help.note}</p>}
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}

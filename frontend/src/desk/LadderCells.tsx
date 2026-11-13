import { num } from "../format";
import { Flash, cx } from "../components/ui";

export function Bar({
  value,
  max,
  side,
}: {
  value: number | null;
  max: number;
  side: "ce" | "pe";
}) {
  const width = value && max ? Math.max(2, (value / max) * 100) : 0;
  return (
    <div className={cx("flex h-3.5 w-full", side === "ce" && "justify-end")}>
      <span
        className={cx(
          "h-full rounded-[1px]",
          side === "ce" ? "bg-call/35" : "bg-put/35",
        )}
        style={{ width: `${width}%` }}
      />
    </div>
  );
}

// In-the-money cells carry a faint wash of their side's colour.
export const TINT = { ce: "bg-call/[0.06]", pe: "bg-put/[0.06]" };

/** A bid or ask that opens its contract's chart, reachable by keyboard like any button. */
export function Price({
  value,
  align,
  itm,
  side,
  dim,
  label,
  onOpen,
}: {
  value: number | null | undefined;
  align: "left" | "right";
  itm: boolean;
  side: "ce" | "pe";
  dim?: boolean;
  label: string;
  onOpen: () => void;
}) {
  return (
    <td className={cx("p-0", itm && TINT[side])}>
      <button
        type="button"
        aria-label={label}
        onClick={onOpen}
        className={cx(
          "block w-full cursor-pointer border-0 bg-transparent px-1 py-1 font-mono hover:bg-hover hover:text-ink",
          align === "right" ? "text-right" : "text-left",
          dim ? "text-ink-2" : "text-ink",
        )}
      >
        <Flash value={value}>{num(value, 2)}</Flash>
      </button>
    </td>
  );
}

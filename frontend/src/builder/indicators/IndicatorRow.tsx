import { Pencil, Trash2 } from "lucide-react";
import type { IndicatorPick, TechCatalog } from "../../api";
import { IndicatorTip } from "../../views/IndicatorTip";
import { indicatorOf, pickText } from "../logic/picks";
import { Button, cx } from "../../components/ui";

function Chip({
  name,
  value,
  used,
}: {
  name?: string;
  value: React.ReactNode;
  used?: boolean;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-sm px-2 py-0.5 text-[12px]",
        used ? "bg-accent-wash text-ink" : "bg-surface-2 text-ink-3",
      )}
    >
      {name && <>{name}:</>}
      <b className="font-semibold text-ink">{value}</b>
    </span>
  );
}

/** One picked indicator: its name, its settings as chips, how many rules read it, and
 * buttons to change or delete it. */
export function IndicatorRow({
  catalog,
  pick,
  uses,
  onEdit,
  onDelete,
}: {
  catalog: TechCatalog;
  pick: IndicatorPick;
  uses: number;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const indicator = indicatorOf(catalog, pick.name);
  const name = pickText(catalog, pick);
  const interval =
    catalog.timeframes.find((t) => t.value === pick.timeframe)?.label ??
    pick.timeframe;
  return (
    <li className="flex flex-col gap-2 rounded-md border border-line px-3 py-2.5 sm:flex-row sm:items-center">
      <IndicatorTip
        indicator={indicator}
        className="min-w-0 sm:w-56 sm:shrink-0"
      >
        <strong
          tabIndex={0}
          className="overflow-hidden text-ellipsis whitespace-nowrap rounded-sm text-[13.5px] font-semibold text-ink"
        >
          {pick.label?.trim() || indicator?.label || pick.name}
        </strong>
      </IndicatorTip>
      <div className="flex min-w-0 flex-1 flex-wrap gap-1.5">
        {pick.label?.trim() && (
          <Chip name="Type" value={indicator?.label ?? pick.name} />
        )}
        <Chip name="Interval" value={interval} />
        <Chip
          name="Chart"
          value={pick.candles === "heikin_ashi" ? "Heikin-Ashi" : "Candles"}
        />
        {(indicator?.params ?? []).map((param) => (
          <Chip
            key={param.name}
            name={param.label}
            value={pick.params[param.name] ?? param.default}
          />
        ))}
        {indicator && indicator.fields.length > 0 && (
          <Chip
            name="Lines"
            value={indicator.fields.map((field) => field.label).join(", ")}
          />
        )}
        <Chip
          used={uses > 0}
          value={
            uses
              ? `In ${uses} rule${uses === 1 ? "" : "s"}`
              : "Not in a rule yet"
          }
        />
      </div>
      <div className="flex shrink-0 gap-1.5 self-end sm:self-auto">
        <Button icon={Pencil} aria-label={`Edit ${name}`} onClick={onEdit}>
          Edit
        </Button>
        <Button
          variant="danger"
          icon={Trash2}
          aria-label={`Delete ${name}`}
          onClick={onDelete}
        >
          Delete
        </Button>
      </div>
    </li>
  );
}

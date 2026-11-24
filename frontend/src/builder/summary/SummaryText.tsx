import type { ReactNode } from "react";
import type { LabSpec, TechCatalog, TechRule } from "../../api";
import { joinWord, ruleText } from "../logic/ruleText";
import { amountText, DECISIONS, hhmm } from "../logic/spec";

function Line({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <span className="text-ink-3">{label}</span> {children}
    </div>
  );
}

function Rules({
  catalog,
  rules,
  lead = "",
  joins = false,
}: {
  catalog: TechCatalog;
  rules: TechRule[];
  lead?: string;
  /** Lead each rule after the first with how it joins the one before: And or Or. */
  joins?: boolean;
}) {
  if (!rules.length) return null;
  return (
    <ul className="list-disc pl-5">
      {rules.map((rule, index) => (
        <li key={index}>
          {lead}
          {joins && index > 0 && (
            <b className="font-semibold">{joinWord(rules, index)} </b>
          )}
          {ruleText(catalog, rule)}
        </li>
      ))}
    </ul>
  );
}

const rupees = (value: number) => `₹${value.toLocaleString("en-IN")}`;

/** The strategy in words: when it enters, what closes it, its daily limits and timing. */
export function SummaryText({
  spec,
  catalog,
}: {
  spec: LabSpec;
  catalog: TechCatalog;
}) {
  const { entry, exits } = spec;
  const conditions = entry.conditions ?? [];
  const daily = [
    exits.daily_stop ? `stop at −${rupees(exits.daily_stop)}` : "",
    exits.daily_target ? `stop at +${rupees(exits.daily_target)}` : "",
    entry.max_trades_per_day
      ? `at most ${entry.max_trades_per_day} trades`
      : "",
    entry.reenter_after_stop === false ? "no re-entry after a stop" : "",
  ].filter(Boolean);
  const exitParts = [
    `profit ${amountText(exits.target_frac, exits.target_unit)}`,
    `loss ${amountText(exits.stop_frac, exits.stop_unit)}`,
    `${exits.exit_dte}d before expiry`,
    exits.square_off ? `square off by ${hhmm(exits.square_off)}` : "",
    exits.trail
      ? exits.trail_step
        ? `stop up ${amountText(exits.trail, exits.trail_unit)} every ${amountText(exits.trail_step, exits.trail_unit)}`
        : `trailing ${amountText(exits.trail, exits.trail_unit)}`
      : "",
    exits.breakeven_at
      ? `to cost after ${amountText(exits.breakeven_at, exits.breakeven_unit)}`
      : "",
  ].filter(Boolean);
  const decision = DECISIONS.find(
    (d) => d.value === (spec.decide_every ?? "day"),
  )?.label.toLowerCase();
  const hours =
    entry.time_from || entry.time_to
      ? `, entering ${hhmm(entry.time_from) || "09:15"}–${hhmm(entry.time_to) || "15:25"}`
      : "";

  return (
    <div className="flex flex-col gap-1.5 text-[13px] leading-snug text-ink">
      <Line label="Enters">
        {entry.dte_min}–{entry.dte_max} days to expiry
        {entry.trend.length ? `, ${entry.trend.join(" or ")} trend` : ""}
        {conditions.length
          ? `, ${conditions.length} market condition${conditions.length > 1 ? "s" : ""}`
          : ""}
      </Line>
      <Rules catalog={catalog} rules={entry.rules ?? []} joins />
      {(entry.any_rules ?? []).length > 0 && (
        <div>
          <span className="text-ink-3">And any of</span>
          <Rules catalog={catalog} rules={entry.any_rules ?? []} />
        </div>
      )}
      <Line label="Exits">{exitParts.join(", ")}</Line>
      <Rules catalog={catalog} rules={exits.rules ?? []} lead="Exit: " />
      {daily.length > 0 && <Line label="Each day">{daily.join(", ")}</Line>}
      <Line label="Decides">
        {decision}
        {hours}
      </Line>
    </div>
  );
}

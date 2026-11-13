import type { Limit } from "../../api";

export type Level = "ok" | "warn" | "breach";

/** Share of a limit used: under 70% is fine, 70 to 100% needs watching, past it is a breach. */
export function levelOf(used: number, limit: number): Level {
  if (limit <= 0) return used > 0 ? "breach" : "ok";
  const share = used / limit;
  if (share >= 1) return "breach";
  if (share >= 0.7) return "warn";
  return "ok";
}

export const FILL: Record<Level, string> = {
  ok: "bg-ink-3",
  warn: "bg-warning",
  breach: "bg-critical",
};

export const TEXT: Record<Level, string> = {
  ok: "text-ink-2",
  warn: "text-warning",
  breach: "text-critical",
};

/** The limits that measure money and positions, in the order the desk reads them. */
export const CAPITAL_KEYS = ["daily_loss", "open_risk", "margin", "positions"];

/** Net Greek limits, which a position can breach in either direction. */
export const GREEK_KEYS = ["delta", "vega", "gamma"] as const;

export function byKey(limits: Limit[], key: string): Limit | undefined {
  return limits.find((limit) => limit.key === key);
}

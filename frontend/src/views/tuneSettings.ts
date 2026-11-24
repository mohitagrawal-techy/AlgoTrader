import type { TuneNode } from "../api";

/** A setting's value in a version: from its saved spec, else from the change's label. */
export function settingValue(
  node: TuneNode,
  path: string,
  side: "new" | "old",
) {
  let holder: unknown = node.spec;
  for (const part of path.split(".")) {
    if (holder === null || typeof holder !== "object") {
      holder = undefined;
      break;
    }
    holder = (holder as Record<string, unknown>)[part];
  }
  if (side === "new" && typeof holder === "number") return holder;
  // Labels read "path: old → new"; an indicator setting at its default is not stored.
  const match = node.label.match(/:\s*(\S+)\s*→\s*(\S+)\s*$/);
  const text = match ? (side === "new" ? match[2] : match[1]) : null;
  const parsed = text === null ? NaN : Number(text);
  return Number.isFinite(parsed) ? parsed : null;
}

const PART: Record<string, string> = {
  op: "condition",
  left: "value",
  right: "compared with",
  params: "",
  timeframe: "candles",
  value: "level",
};

/** A readable name for a path: its own label, else the label of the rule or setting
 * holding it with the part changed, as in "Entry rule 2: … › condition". */
export function settingName(
  path: string,
  labels: Record<string, string>,
): string {
  if (labels[path]) return labels[path];
  const parts = path.split(".");
  for (let end = parts.length - 1; end > 0; end -= 1) {
    const holder = labels[parts.slice(0, end).join(".")];
    if (holder) {
      const rest = parts
        .slice(end)
        .map((part) => PART[part] ?? part)
        .filter(Boolean)
        .join(" ");
      // "Rule 2 (5m) Low below …" or "Entry rule 2: …" names the rule by its number.
      const rule = holder.split(/ \(|:/)[0];
      return `${rule} › ${rest}`;
    }
  }
  return path;
}

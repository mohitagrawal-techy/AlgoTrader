import type { ReactNode } from "react";
import { FeatureHelp, type FeatureHelpContent } from "../../views/FeatureHelp";
import { cx } from "../ui/Button";
import { Switch } from "../ui/choices";

/** One optional feature: a switch, what it does, a worked example behind the info button,
 * and its settings while it is on. */
export function FeatureCard({
  id,
  label,
  hint,
  help,
  checked,
  disabled,
  onChange,
  children,
}: {
  id: string;
  label: string;
  hint: string;
  help: FeatureHelpContent;
  checked: boolean;
  disabled?: boolean;
  onChange: (on: boolean) => void;
  children?: ReactNode;
}) {
  const on = checked && !disabled;
  return (
    <div
      className={cx(
        "flex flex-col gap-1.5 rounded-md border p-3 transition-colors",
        on ? "border-accent/45 bg-accent-wash" : "border-line",
      )}
    >
      <div className="flex items-center gap-1.5">
        <Switch
          id={id}
          checked={on}
          disabled={disabled}
          describedBy={`${id}-hint`}
          onChange={onChange}
        >
          {label}
        </Switch>
        <FeatureHelp title={label} help={help} />
      </div>
      <p
        id={`${id}-hint`}
        className={cx(
          "text-[12.5px] leading-snug text-ink-3",
          disabled && "opacity-80",
        )}
      >
        {hint}
      </p>
      {on && children && <div className="mt-1.5">{children}</div>}
    </div>
  );
}

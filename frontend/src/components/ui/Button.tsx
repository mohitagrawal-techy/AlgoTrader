import type { LucideIcon } from "lucide-react";
import type { AnchorHTMLAttributes, ButtonHTMLAttributes } from "react";
import { twMerge } from "tailwind-merge";

/** Joins class names, skipping the empty ones. When two set the same property, such as
 * `h-9` and `h-8`, the later one wins, so a caller's class overrides a default. */
export function cx(...names: (string | false | null | undefined)[]): string {
  return twMerge(names.filter(Boolean).join(" "));
}

export type ButtonVariant =
  | "primary"
  | "secondary"
  | "ghost"
  | "danger"
  | "danger-solid"
  | "link"
  | "link-danger";

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "border-accent bg-accent text-white hover:border-accent-ink/60 hover:brightness-110",
  secondary: "border-line bg-surface text-ink hover:border-axis hover:bg-hover",
  ghost:
    "border-transparent bg-transparent text-ink-2 hover:bg-hover hover:text-ink",
  danger: "border-down/40 bg-transparent text-down hover:bg-critical-wash",
  "danger-solid": "border-down bg-down text-white hover:opacity-90",
  link: cx(
    "h-auto border-transparent bg-transparent px-0 text-ink underline decoration-axis",
    "underline-offset-3 hover:decoration-accent disabled:text-ink-3",
  ),
  "link-danger": cx(
    "h-auto border-transparent bg-transparent px-0 text-down underline decoration-down/40",
    "underline-offset-3 hover:decoration-down",
  ),
};

const BASE = cx(
  "inline-flex cursor-pointer items-center justify-center gap-1.5 rounded-md border py-0 font-semibold",
  "whitespace-nowrap no-underline transition-colors",
);

const SIZES = {
  sm: "h-8 px-2.5 text-[13px]",
  md: "h-9 px-3.5 text-[13.5px]",
  icon: "size-8 p-0",
};

/** The page's buttons: one look per job, a clear disabled state, and an optional icon. */
export function Button({
  variant = "secondary",
  size = "sm",
  icon: Icon,
  className,
  children,
  type = "button",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: keyof typeof SIZES;
  icon?: LucideIcon;
}) {
  return (
    <button
      type={type}
      className={cx(
        BASE,
        "disabled:cursor-not-allowed disabled:opacity-50",
        SIZES[size],
        VARIANTS[variant],
        className,
      )}
      {...rest}
    >
      {Icon && <Icon aria-hidden className="size-4 shrink-0" strokeWidth={2} />}
      {children}
    </button>
  );
}

/** A link styled as a button, for moving to another page. */
export function ButtonLink({
  variant = "secondary",
  size = "sm",
  icon: Icon,
  className,
  children,
  ...rest
}: AnchorHTMLAttributes<HTMLAnchorElement> & {
  href: string;
  variant?: ButtonVariant;
  size?: keyof typeof SIZES;
  icon?: LucideIcon;
}) {
  return (
    <a
      className={cx(BASE, SIZES[size], VARIANTS[variant], className)}
      {...rest}
    >
      {Icon && <Icon aria-hidden className="size-4 shrink-0" strokeWidth={2} />}
      {children}
    </a>
  );
}

import { X } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";
import { Button, cx } from "./Button";

/** A modal over the page. The browser's own dialog keeps keyboard focus inside it. Escape,
 * the close button and a click on the backdrop all call `onClose`, which should unmount it;
 * focus then returns to where it was. Mount it to open it.
 *
 * `onClose` is called directly rather than from the dialog's "close" event, which some
 * embedded browsers never deliver. */
export function Dialog({
  title,
  description,
  size = "md",
  footer,
  onClose,
  as = "div",
  onSubmit,
  children,
}: {
  title: ReactNode;
  description?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
  footer?: ReactNode;
  onClose: () => void;
  /** "form" makes the dialog one form, its footer's submit button sending it. */
  as?: "div" | "form";
  onSubmit?: () => void;
  children: ReactNode;
}) {
  const titleId = useId();
  const box = useRef<HTMLDialogElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  // What had focus when it opened, read on the first render, before the dialog takes it.
  const opener = useRef(document.activeElement as HTMLElement | null);

  useEffect(() => {
    const dialog = box.current;
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    // Escape asks to cancel; the page closes it by unmounting, so the request stops here.
    const cancel = (event: Event) => {
      event.preventDefault();
      close.current();
    };
    // Any other way it closes, such as a form inside it with method="dialog".
    const closed = () => close.current();
    dialog.addEventListener("cancel", cancel);
    dialog.addEventListener("close", closed);
    return () => {
      dialog.removeEventListener("cancel", cancel);
      dialog.removeEventListener("close", closed);
      // Unmounting removes the dialog without closing it, so focus is returned here.
      const before = opener.current;
      if (before?.isConnected) before.focus();
    };
  }, []);

  const width = {
    sm: "max-w-md",
    md: "max-w-xl",
    lg: "max-w-4xl",
    xl: "max-w-6xl",
  }[size];
  const Body = as;
  return (
    <dialog
      ref={box}
      aria-labelledby={titleId}
      onClick={(event) => {
        if (event.target === event.currentTarget) close.current();
      }}
      className={cx(
        "m-auto max-h-[min(90vh,900px)] w-[calc(100vw-24px)] overflow-hidden rounded-lg border border-line p-0",
        "bg-surface text-ink shadow-pop backdrop:bg-[rgb(0_0_0/0.5)]",
        width,
      )}
    >
      <Body
        className="flex max-h-[min(90vh,900px)] flex-col"
        onSubmit={
          as === "form"
            ? (event: React.FormEvent) => {
                event.preventDefault();
                onSubmit?.();
              }
            : undefined
        }
      >
        <header className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 id={titleId} className="text-[16px] font-semibold text-ink">
              {title}
            </h2>
            {description && (
              <p className="mt-1 text-[12.5px] leading-snug text-ink-3">
                {description}
              </p>
            )}
          </div>
          <Button
            variant="ghost"
            size="icon"
            icon={X}
            aria-label="Close"
            onClick={() => close.current()}
          />
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {children}
        </div>
        {footer && (
          <footer className="flex flex-wrap justify-end gap-2 border-t border-line px-5 py-3">
            {footer}
          </footer>
        )}
      </Body>
    </dialog>
  );
}

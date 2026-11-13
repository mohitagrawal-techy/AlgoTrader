import { RotateCw } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { Alert } from "./ui/Alert";
import { Button, ButtonLink } from "./ui/Button";

/** The page's shape while the lab loads, so nothing jumps when it arrives. */
export function BuilderSkeleton() {
  const bar = "rounded-sm bg-surface-2 motion-safe:animate-pulse";
  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-3">
      <span className="sr-only">Loading the strategy builder…</span>
      <div className={`${bar} h-8 w-64`} />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="flex flex-col gap-3">
          {[96, 220, 160, 140].map((height, index) => (
            <div
              key={index}
              className="rounded-lg border border-line bg-surface p-4"
              style={{ height }}
            >
              <div className={`${bar} h-4 w-32`} />
              <div className={`${bar} mt-3 h-3 w-3/4`} />
            </div>
          ))}
        </div>
        <div className="hidden h-80 rounded-lg border border-line bg-surface p-4 xl:block">
          <div className={`${bar} h-4 w-24`} />
        </div>
      </div>
    </div>
  );
}

/** Why the builder cannot show, and what to do: retry, or go back to the lab. */
export function BuilderProblem({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="max-w-xl">
      <Alert
        tone="critical"
        title={title}
        action={
          <div className="flex flex-wrap gap-2">
            {onRetry && (
              <Button variant="primary" icon={RotateCw} onClick={onRetry}>
                Try again
              </Button>
            )}
            <ButtonLink href="#lab" variant="secondary">
              Back to the lab
            </ButtonLink>
          </div>
        }
      >
        {message}
      </Alert>
    </div>
  );
}

/** Catches a crash inside the builder, so one broken part shows a message with a retry
 * instead of a blank page. Retrying draws the builder again from its saved state. */
export class BuilderErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Strategy builder crashed", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <BuilderProblem
        title="Something in the builder broke"
        message={`${this.state.error.message}. Your last saved version is safe; unsaved changes on this page may be lost.`}
        onRetry={() => this.setState({ error: null })}
      />
    );
  }
}

import { useMemo, useState, type ReactNode } from "react";
import { AiButton, AiExplain, type AiTarget } from "../../assistant";

/** A titled card on a page. With `ai`, its header offers an explanation of what it shows. */
export function Panel({
  title,
  aside,
  ai,
  children,
}: {
  title: string;
  aside?: ReactNode;
  ai?: AiTarget;
  children: ReactNode;
}) {
  const [explaining, setExplaining] = useState(false);
  // A stable target means re-renders do not restart an explanation. New settings do.
  const key = ai ? JSON.stringify([ai.component, ai.params ?? {}]) : "";
  const target = useMemo(() => ai, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <section className="panel">
      <header className="panel-header">
        <h2>{title}</h2>
        {(aside || ai) && (
          <div className="panel-aside">
            {aside}
            {ai && (
              <AiButton
                active={explaining}
                label={title}
                onClick={() => setExplaining((value) => !value)}
              />
            )}
          </div>
        )}
      </header>
      {target && explaining && (
        <AiExplain target={target} onClose={() => setExplaining(false)} />
      )}
      {children}
    </section>
  );
}

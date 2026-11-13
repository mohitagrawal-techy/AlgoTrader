import { Copy, FlipHorizontal2, Plus } from "lucide-react";
import type { ReadyBuilder } from "../hooks/useStrategyBuilder";
import { tabOf } from "../logic/problems";
import { oppositeCase } from "../logic/ruleLists";
import { MAX_CASES } from "../logic/spec";
import { Button } from "../ui/Button";
import { Badge, Section } from "../ui/Section";
import { CasePanel } from "./CasePanel";
import { CaseTabs } from "./CaseTabs";

/** Each case is a way into the strategy, with its own rules and legs, on tabs. */
export function CasesSection({ b }: { b: ReadyBuilder }) {
  const c = b.cases;
  const withProblems = new Set(
    Object.entries(b.visible)
      .filter(([, text]) => text)
      .map(([key]) => tabOf(key))
      .filter((tab): tab is number => tab !== null),
  );
  return (
    <Section
      title="Cases"
      hint="Each case is a way into the strategy, with its own entry rules, exit rules and legs. The first case whose rules hold trades."
      aside={
        <Badge>
          {c.count} of {MAX_CASES + 1}
        </Badge>
      }
    >
      <div className="flex flex-wrap items-end justify-between gap-2 border-b border-line">
        <CaseTabs
          cases={c.cases}
          active={c.active}
          withProblems={withProblems}
          onSelect={c.setActive}
        />
        {c.cases.length < MAX_CASES && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            <Button icon={Copy} onClick={c.duplicateCase}>
              Duplicate
            </Button>
            <Button
              icon={FlipHorizontal2}
              onClick={() => c.addCase(oppositeCase(b.spec))}
            >
              Add opposite case
            </Button>
            <Button variant="primary" icon={Plus} onClick={c.addBlankCase}>
              Add case
            </Button>
          </div>
        )}
      </div>
      <CasePanel b={b} />
    </Section>
  );
}

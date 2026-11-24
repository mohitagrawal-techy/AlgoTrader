import { Plus, X } from "lucide-react";
import type { LabCondition } from "../../api";
import type { ReadyBuilder } from "../hooks/useStrategyBuilder";
import { MAX_CONDITIONS } from "../logic/spec";
import {
  Button,
  FieldMessage,
  NumberInput,
  Select,
  Badge,
} from "../../components/ui";

/** Market conditions such as "India VIX above 15": a signal, a direction and a level. */
export function ConditionsEditor({ b }: { b: ReadyBuilder }) {
  const conditions = b.spec.entry.conditions ?? [];
  const signals = b.lab.signals;
  const set = (next: LabCondition[]) => b.edit.entry({ conditions: next });
  const change = (index: number, part: Partial<LabCondition>) =>
    set(
      conditions.map((item, i) => (i === index ? { ...item, ...part } : item)),
    );

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h3 className="text-[13.5px] font-semibold">Market conditions</h3>
        <Badge>
          {conditions.length} of {MAX_CONDITIONS}
        </Badge>
      </div>
      {conditions.length > 0 && (
        <ul className="flex flex-col gap-2">
          {conditions.map((condition, index) => {
            const id = `condition-${index}`;
            return (
              <li
                key={index}
                className="grid grid-cols-[1fr_auto] gap-2 sm:grid-cols-[1fr_8rem_8rem_auto]"
              >
                <Select
                  id={`${id}-signal`}
                  aria-label="Signal"
                  className="col-span-2 sm:col-span-1"
                  value={condition.signal}
                  onChange={(event) =>
                    change(index, { signal: event.target.value })
                  }
                >
                  {signals.map((signal) => (
                    <option key={signal.name} value={signal.name}>
                      {signal.label}
                    </option>
                  ))}
                </Select>
                <Select
                  id={`${id}-op`}
                  aria-label="Direction"
                  value={condition.op}
                  onChange={(event) =>
                    change(index, {
                      op: event.target.value as LabCondition["op"],
                    })
                  }
                >
                  <option value="above">Above</option>
                  <option value="below">Below</option>
                </Select>
                <div>
                  <NumberInput
                    id={id}
                    label="Level"
                    value={condition.value}
                    step={0.5}
                    invalid={!!b.visible[id]}
                    onChange={(value) =>
                      change(index, { value: value as number })
                    }
                  />
                  <FieldMessage id={id} problem={b.visible[id]} />
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  icon={X}
                  aria-label={`Remove condition ${index + 1}`}
                  className="hover:text-down"
                  onClick={() => set(conditions.filter((_, i) => i !== index))}
                />
              </li>
            );
          })}
        </ul>
      )}
      {conditions.length < MAX_CONDITIONS && signals.length > 0 && (
        <Button
          icon={Plus}
          className="self-start"
          onClick={() =>
            set([
              ...conditions,
              { signal: signals[0].name, op: "above", value: 0 },
            ])
          }
        >
          Add a condition
        </Button>
      )}
    </div>
  );
}

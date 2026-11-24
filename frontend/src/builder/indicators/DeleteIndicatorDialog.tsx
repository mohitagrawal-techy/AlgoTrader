import { Trash2 } from "lucide-react";
import type { IndicatorPick, TechCatalog, TechRule } from "../../api";
import { pickText } from "../logic/picks";
import { ruleText } from "../logic/ruleText";
import { Button, Hint, Dialog } from "../../components/ui";

/** Asks before deleting an indicator that rules read, listing those rules, which go too. */
export function DeleteIndicatorDialog({
  catalog,
  pick,
  uses,
  onConfirm,
  onClose,
}: {
  catalog: TechCatalog;
  pick: IndicatorPick;
  uses: { where: string; rule: TechRule }[];
  onConfirm: () => void;
  onClose: () => void;
}) {
  const one = uses.length === 1;
  return (
    <Dialog
      size="sm"
      title={`Delete ${pickText(catalog, pick)}?`}
      description={`${one ? "A rule reads it" : `${uses.length} rules read it`}, so ${one ? "that rule goes" : "those rules go"} too.`}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="danger-solid" icon={Trash2} onClick={onConfirm}>
            Delete it and {one ? "its rule" : `its ${uses.length} rules`}
          </Button>
        </>
      }
    >
      <ul className="mb-3 flex list-disc flex-col gap-1.5 pl-5 text-[13.5px]">
        {uses.map(({ where, rule }, i) => (
          <li key={i}>
            <span className="text-ink-3">{where}: </span>
            {ruleText(catalog, rule)}
          </li>
        ))}
      </ul>
      <Hint>
        To keep a rule, cancel and choose another indicator in it first.
      </Hint>
    </Dialog>
  );
}

import type { IndicatorPick } from "../../api";
import { Button, Dialog } from "../../components/ui";
import { IndicatorChooser } from "./IndicatorChooser";
import { IndicatorSettings } from "./IndicatorSettings";
import {
  useIndicatorForm,
  type IndicatorFormOptions,
} from "./useIndicatorForm";

/** A popup form to add an indicator to a strategy, or change one's settings. */
export function IndicatorForm({
  onSave,
  onClose,
  ...options
}: IndicatorFormOptions & {
  onSave: (pick: IndicatorPick) => void;
  onClose: () => void;
}) {
  const form = useIndicatorForm(options);
  const { editing } = options;
  return (
    <Dialog
      size="lg"
      as="form"
      title={
        editing
          ? `Change ${form.chosen?.label ?? "indicator"}`
          : "Add an indicator"
      }
      description={
        editing
          ? "New settings apply to every rule that uses it."
          : "Every case's entry and exit rules can then use it."
      }
      onClose={onClose}
      onSubmit={() => {
        const pick = form.submit();
        if (pick) onSave(pick);
      }}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={!form.chosen}>
            {editing ? "Save changes" : "Add indicator"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5 md:flex-row">
        {!editing && <IndicatorChooser form={form} />}
        <IndicatorSettings
          catalog={options.catalog}
          form={form}
          intraday={options.intraday}
        />
      </div>
    </Dialog>
  );
}

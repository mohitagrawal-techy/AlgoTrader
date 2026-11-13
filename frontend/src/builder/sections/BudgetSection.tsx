import type { ReadyBuilder } from "../hooks/useStrategyBuilder";
import {
  Segmented,
  Field,
  GroupLabel,
  Hint,
  NumberInput,
  Section,
} from "../../components/ui";

const rupees = (value: number) =>
  `₹${Math.round(value).toLocaleString("en-IN")}`;

export function BudgetSection({ b }: { b: ReadyBuilder }) {
  const { budget, edit, visible } = b;
  const spend = (budget.sizing ?? "budget") === "budget";
  return (
    <Section
      title="Budget"
      hint="Each strategy is tested as its own account. Lots are sized so one trade never risks more than its share, and returns and drawdowns are judged against this capital."
    >
      <div className="mb-4 flex flex-col gap-1.5">
        <GroupLabel id="sizing-label">Lots per trade</GroupLabel>
        <Segmented
          name="sizing"
          label="How lots are sized"
          value={budget.sizing ?? "budget"}
          className="w-full sm:w-auto"
          options={[
            { value: "budget", label: "Spend the budget" },
            { value: "risk", label: "Limit the risk" },
          ]}
          onChange={(sizing) => edit.budget({ sizing })}
        />
        <Hint>
          {spend
            ? "As many lots as the money allows: a ₹100 option is ₹6,500 a Nifty lot, so ₹1,00,000 buys 15 lots. Money in open positions is not available again until they close."
            : "As many lots as keep one trade's planned loss within the risk per trade, inside the account's delta, gamma, vega and stress limits."}
        </Hint>
      </div>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
        <Field
          id="capital"
          label="Capital, ₹"
          problem={visible.capital}
          className="col-span-2 md:col-span-1"
        >
          <NumberInput
            id="capital"
            value={budget.capital}
            step={10000}
            invalid={!!visible.capital}
            onChange={(value) => edit.budget({ capital: value as number })}
          />
        </Field>
        {spend ? (
          <Field
            id="deploy"
            label="Spend per trade, % of capital"
            problem={visible.deploy_pct}
            hint={`Up to ${rupees((budget.capital * (budget.deploy_pct ?? 100)) / 100)} a trade`}
          >
            <NumberInput
              id="deploy"
              value={budget.deploy_pct ?? 100}
              step={5}
              invalid={!!visible.deploy_pct}
              onChange={(value) => edit.budget({ deploy_pct: value as number })}
            />
          </Field>
        ) : (
          <Field
            id="risk-per-trade"
            label="Risk per trade, %"
            problem={visible.risk_per_trade_pct}
            hint={
              Number.isFinite(budget.capital * budget.risk_per_trade_pct)
                ? `Up to ${rupees((budget.capital * budget.risk_per_trade_pct) / 100)} at risk a trade`
                : undefined
            }
          >
            <NumberInput
              id="risk-per-trade"
              value={budget.risk_per_trade_pct}
              step={0.5}
              invalid={!!visible.risk_per_trade_pct}
              onChange={(value) =>
                edit.budget({ risk_per_trade_pct: value as number })
              }
            />
          </Field>
        )}
        <Field
          id="max-open"
          label="Open positions at once"
          problem={visible.max_open}
        >
          <NumberInput
            id="max-open"
            value={budget.max_open}
            invalid={!!visible.max_open}
            onChange={(value) => edit.budget({ max_open: value as number })}
          />
        </Field>
      </div>
      <Hint className="mt-3">
        {spend
          ? "Spending the budget can lose much more than a risk-sized trade: losses grow with the lots. Use the exit rules to cap them."
          : "Margin and the day's loss limit are shares of this capital too. The account's limits on delta, gamma, vega and stress losses still apply in rupees."}
      </Hint>
    </Section>
  );
}

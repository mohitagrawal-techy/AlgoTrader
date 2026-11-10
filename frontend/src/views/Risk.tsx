import type { Limit, Portfolio } from "../api";
import { inr, num } from "../format";
import { Meter, Panel, Status, Tile, type Tone } from "../ui";

function toneFor(share: number): Tone | undefined {
  if (share >= 90) return "critical";
  if (share >= 70) return "warning";
  return undefined;
}

function LimitRow({ limit }: { limit: Limit }) {
  const share = limit.limit > 0 ? (limit.used / limit.limit) * 100 : 0;
  const format = limit.unit === "money" ? inr : (value: number) => num(value);
  const tone = toneFor(share);
  return (
    <div className="limit">
      <div className="limit-head">
        <span>{limit.label}</span>
        <span className="limit-values">
          {format(limit.used)} <small>of {format(limit.limit)}</small>
        </span>
      </div>
      <Meter value={share} tone={tone} />
      {tone && (
        <Status tone={tone}>{share >= 100 ? "At limit" : `${Math.round(share)}% used`}</Status>
      )}
    </div>
  );
}

export function RiskView({ portfolio }: { portfolio: Portfolio }) {
  return (
    <>
      <div className="tiles tiles-3">
        <Tile label="Paper capital" value={inr(portfolio.capital)} />
        <Tile
          label="Today's trading"
          value={
            portfolio.halted ? (
              <Status tone="critical">Halted, daily loss limit</Status>
            ) : (
              <Status tone="good">Active</Status>
            )
          }
          hint={portfolio.halted ? "New entries resume next session" : undefined}
        />
        <Tile
          label="Automatic paper trades"
          value={
            portfolio.auto_trade ? (
              <Status tone="good">On</Status>
            ) : (
              <Status tone="neutral">Off</Status>
            )
          }
          hint={portfolio.auto_trade ? `Takes ideas scoring ${num(portfolio.min_score)} or more` : undefined}
        />
      </div>
      <Panel title="Limit usage" aside={<span className="muted">Edit in config/risk.yaml</span>}>
        <div className="limits">
          {portfolio.limits.map((limit) => (
            <LimitRow key={limit.key} limit={limit} />
          ))}
        </div>
      </Panel>
    </>
  );
}

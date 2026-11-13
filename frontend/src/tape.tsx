import { useEffect, useState } from "react";
import { api, type IndexRow } from "./api";
import { num } from "./format";
import { Pnl } from "./components/ui";

const POLL_MS = 30000;
// The underlyings this desk trades, then the volatility gauge.
const WANTED = ["Nifty 50", "Bank Nifty", "Fin Nifty", "Nifty Financial Services", "Sensex"];
const MAX_ITEMS = 4;

function pick(indices: IndexRow[]): IndexRow[] {
  const chosen = WANTED.map((label) => indices.find((row) => row.label === label)).filter(
    (row): row is IndexRow => !!row,
  );
  for (const row of indices) {
    if (chosen.length >= MAX_ITEMS) break;
    if (!chosen.includes(row)) chosen.push(row);
  }
  return chosen.slice(0, MAX_ITEMS);
}

/** Live index readouts across the top of every page. */
export function MarketTape() {
  const [rows, setRows] = useState<IndexRow[]>([]);

  useEffect(() => {
    let active = true;
    const load = () =>
      api
        .overview()
        .then((data) => {
          if (!active) return;
          const vix = data.volatility ? [data.volatility] : [];
          setRows([...pick(data.indices), ...vix]);
        })
        .catch(() => undefined);
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  if (!rows.length) return <div className="tape" />;
  return (
    <div className="tape" aria-label="Index levels">
      {rows.map((row) => {
        const change = row.change_pct;
        return (
          <a className="tape-item" href="#overview" key={row.name} title={`${row.label}: open the overview`}>
            <span className="tape-name">{row.label}</span>
            <span className="tape-last">{num(row.last, 2)}</span>
            <span className="tape-change">
              <Pnl value={change}>
                {change === null ? "–" : `${change > 0 ? "+" : ""}${change.toFixed(2)}%`}
              </Pnl>
            </span>
          </a>
        );
      })}
    </div>
  );
}

import type { Portfolio } from "../api";
import { RiskMonitor } from "../desk/risk/RiskMonitor";

export function RiskView({ portfolio }: { portfolio: Portfolio }) {
  return <RiskMonitor portfolio={portfolio} />;
}

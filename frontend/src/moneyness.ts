export type Moneyness = "ITM" | "ATM" | "OTM";

export interface StrikePosition {
  state: Moneyness;
  /** Strikes away from the at-the-money strike: 0 for ATM, 1 for the next one out, and so on. */
  steps: number;
  /** "ATM", "ITM+2", "OTM+1". */
  label: string;
}

/** The listed strike nearest to spot. */
export function atmStrike(strikes: number[], spot: number): number {
  return strikes.reduce(
    (best, strike) => (Math.abs(strike - spot) < Math.abs(best - spot) ? strike : best),
    strikes[0] ?? 0,
  );
}

/**
 * Where a strike sits against the at-the-money strike, counted in listed strikes. A call below
 * ATM is in the money and a put below ATM is out of the money, and the reverse above it.
 */
export function strikePosition(
  strikes: number[],
  strike: number,
  atm: number,
  isCall: boolean,
): StrikePosition {
  const sorted = [...new Set(strikes)].sort((a, b) => a - b);
  const steps = Math.abs(sorted.indexOf(strike) - sorted.indexOf(atm));
  if (strike === atm || !sorted.includes(strike) || !sorted.includes(atm)) {
    const state: Moneyness = strike === atm ? "ATM" : strike < atm === isCall ? "ITM" : "OTM";
    return { state, steps: strike === atm ? 0 : 1, label: state };
  }
  const state: Moneyness = strike < atm === isCall ? "ITM" : "OTM";
  return { state, steps, label: `${state}+${steps}` };
}

import { useEffect, useState } from "react";
import type { ForwardOverview, MarketPayload, Portfolio } from "../api";
import { Segmented } from "../components/ui";
import { OverviewView } from "../views/Overview";
import { Desk } from "./Desk";

type View = "desk" | "market";
const KEY = "algoverse.command-center.view";

function readView(): View {
  try {
    return localStorage.getItem(KEY) === "market" ? "market" : "desk";
  } catch {
    return "desk";
  }
}

/** The first screen: the paper desk, or the wider market dashboard, one switch apart. */
export function CommandCenter(props: {
  portfolio: Portfolio | undefined;
  forward: ForwardOverview | undefined;
  market: MarketPayload | null | undefined;
}) {
  const [view, setView] = useState<View>(readView);
  useEffect(() => {
    try {
      localStorage.setItem(KEY, view);
    } catch {
      // The switch still works for this visit when storage is blocked.
    }
  }, [view]);
  return (
    <>
      <Segmented
        label="View"
        value={view}
        onChange={setView}
        className="self-start"
        options={[
          { value: "desk", label: "Desk" },
          { value: "market", label: "Market" },
        ]}
      />
      {view === "desk" ? <Desk {...props} /> : <OverviewView />}
    </>
  );
}

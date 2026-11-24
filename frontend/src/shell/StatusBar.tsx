import { useEffect, useState } from "react";
import type { Health } from "../api";
import { age } from "../format";

const IST = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

/** The facts that say whether the screen can be trusted, always in view at the bottom:
 * paper mode, the broker session, the market, how old the data is and how fast the server
 * answers. */
export function StatusBar({
  health,
  offline,
  latencyMs,
}: {
  health: Health | undefined;
  offline: boolean;
  latencyMs: number | null;
}) {
  const [clock, setClock] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const serverNow = health ? new Date(health.server_time).getTime() : 0;
  const session = !health
    ? null
    : !health.upstox_configured
      ? { tone: "warn", text: "Upstox not set up" }
      : health.broker_session_valid
        ? { tone: "ok", text: "Upstox connected" }
        : { tone: "warn", text: "Upstox logged out" };

  return (
    <footer className="statusbar" aria-label="Connection status">
      <span
        className="status-paper"
        title="No real orders can be placed from this app"
      >
        Paper
      </span>
      {offline ? (
        <span className="status-item" data-tone="bad">
          Server unreachable
        </span>
      ) : (
        <>
          {session && (
            <span className="status-item" data-tone={session.tone}>
              {session.text}
            </span>
          )}
          {health && (
            <span
              className="status-item"
              data-tone={health.market_open ? "ok" : "idle"}
            >
              {health.market_open ? "Market open" : "Market closed"}
            </span>
          )}
          {health && (
            <span className="status-item status-wide">
              Data {age(health.data_as_of, serverNow)}
            </span>
          )}
          {latencyMs !== null && (
            <span className="status-item status-wide" title="Server round trip">
              API {Math.round(latencyMs)} ms
            </span>
          )}
        </>
      )}
      <span className="status-clock">
        {IST.format(clock)} <span className="status-zone">IST</span>
      </span>
    </footer>
  );
}

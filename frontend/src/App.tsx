import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type BacktestListing,
  type ForwardOverview,
  type Health,
  type IdeasPayload,
  type MarketPayload,
  type Portfolio,
} from "./api";
import { age } from "./format";
import { MarketTape } from "./tape";
import { Empty, Status } from "./ui";
import { AssistantProvider } from "./assistant";
import { BacktestsView } from "./views/Backtests";
import { ForwardView } from "./views/Forward";
import { IdeasView } from "./views/Ideas";
import { LabView } from "./views/Lab";
import { TestHistoryView } from "./views/TestHistory";
import { LabBacktestView } from "./views/LabBacktest";
import { MarketView, readSavedMarket, saveMarket } from "./views/Market";
import { OverviewView } from "./views/Overview";
import { PaperView } from "./views/Paper";
import { RiskView } from "./views/Risk";
import { StrategyEditor, type EditorRoute } from "./builder/StrategyBuilder";
import { readStrikeRoute, StrikePage } from "./views/StrikeChart";
import { TunerView } from "./views/Tuner";

const NAV_GROUPS = [
  {
    title: "Market",
    items: [
      {
        id: "overview",
        label: "Overview",
        icon: "M3 3v18h18M7 15l4-5 3 3 5-7",
      },
      {
        id: "market",
        label: "Options",
        icon: "M4 6h16M4 12h16M4 18h16M10 4v16",
      },
      {
        id: "ideas",
        label: "Ideas",
        icon: "M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z",
      },
    ],
  },
  {
    title: "Strategies",
    items: [
      {
        id: "lab",
        label: "Strategy lab",
        icon: "M9 3h6M10 3v6l-5.5 9.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.5-2.5L14 9V3M7.5 15h9",
      },
      {
        id: "backtests",
        label: "Backtests",
        icon: "M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2",
      },
      {
        id: "history",
        label: "Test history",
        icon: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
      },
      { id: "forward", label: "Forward test", icon: "M5 12h14M13 6l6 6-6 6" },
    ],
  },
  {
    title: "Portfolio",
    items: [
      {
        id: "paper",
        label: "Paper trades",
        icon: "M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h7",
      },
      {
        id: "risk",
        label: "Risk",
        icon: "M12 3l8 3v6c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V6z",
      },
    ],
  },
] as const;
const TABS = NAV_GROUPS.flatMap((group) => [...group.items]);
type TabId = (typeof NAV_GROUPS)[number]["items"][number]["id"];
const SIDEBAR_KEY = "algoverse-sidebar";

const PAGE_SUBS: Record<string, string> = {
  overview: "Indices, breadth and option positioning across the market.",
  market: "Chain analytics for one underlying and expiry.",
  ideas: "Trades ranked from the latest option chains.",
  lab: "Build your own strategies, then test and tune them.",
  backtests: "How the idea engine would have traded past sessions.",
  history: "Every test, tuner check and ladder step run so far, newest first.",
  forward: "Locked configurations paper-trading on live data.",
  paper: "Paper positions, fills and P&L after costs.",
  risk: "Limits and exposure across open paper positions.",
};

const POLL_MS = 30000;
const STALE_MS = 15 * 60000;

function tabFromHash(): TabId {
  const first = window.location.hash.slice(1).split("/")[0];
  return TABS.some((tab) => tab.id === first) ? (first as TabId) : "overview";
}

function labPath(): string[] {
  const [first, ...rest] = window.location.hash.slice(1).split("/");
  return first === "lab" ? rest.map(decodeURIComponent) : [];
}

function editorRoute(path: string[]): EditorRoute | null {
  if (path[0] === "new") return { mode: "new" };
  if ((path[0] === "edit" || path[0] === "copy") && path[1])
    return { mode: path[0], id: path[1] };
  return null;
}

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "collapsed";
  } catch {
    return false;
  }
}

function NavIcon({ path }: { path: string }) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" aria-hidden>
      <path
        d={path}
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function readTheme(): string | null {
  try {
    return localStorage.getItem("algoverse-theme");
  } catch {
    return null;
  }
}

function useTheme() {
  const [theme, setTheme] = useState<string | null>(readTheme);
  useEffect(() => {
    if (theme) document.documentElement.dataset.theme = theme;
    else delete document.documentElement.dataset.theme;
    try {
      if (theme) localStorage.setItem("algoverse-theme", theme);
    } catch {
      // The toggle still works for this visit when storage is blocked.
    }
  }, [theme]);
  const toggle = () => {
    const prefersDark = window.matchMedia(
      "(prefers-color-scheme: dark)",
    ).matches;
    const isDark = theme ? theme === "dark" : prefersDark;
    setTheme(isDark ? "light" : "dark");
  };
  return toggle;
}

interface Data {
  health: Health;
  ideas: IdeasPayload;
  market: MarketPayload;
  portfolio: Portfolio;
  backtests: BacktestListing;
  forward: ForwardOverview;
}

export function App() {
  const [tab, setTab] = useState<TabId>(tabFromHash);
  const [path, setPath] = useState<string[]>(labPath);
  const [strikeRoute, setStrikeRoute] = useState(readStrikeRoute);
  const route = useMemo(() => editorRoute(path), [path.join("/")]); // eslint-disable-line react-hooks/exhaustive-deps
  const [data, setData] = useState<Data | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Until the server answers, Options shows the chains this browser saved last time.
  const [savedMarket] = useState(readSavedMarket);
  const shownMarket = data?.market ?? savedMarket;
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const toggleTheme = useTheme();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_KEY, collapsed ? "collapsed" : "expanded");
    } catch {
      // The sidebar still works for this visit when storage is blocked.
    }
  }, [collapsed]);

  const load = useCallback(async () => {
    try {
      const [health, ideas, market, portfolio, backtests, forward] =
        await Promise.all([
          api.health(),
          api.ideas(),
          api.market(),
          api.portfolio(),
          api.backtests(),
          api.forward(),
        ]);
      setData({ health, ideas, market, portfolio, backtests, forward });
      saveMarket(market);
      setLoadError(null);
    } catch {
      setLoadError(
        "Cannot reach the AlgoVerse server. Is `algoverse serve` running?",
      );
    }
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    const onHash = () => {
      setTab(tabFromHash());
      setPath(labPath());
      setStrikeRoute(readStrikeRoute());
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const refresh = async () => {
    setRefreshing(true);
    setRefreshError(null);
    try {
      await api.refresh();
      await load();
    } catch (error) {
      setRefreshError(
        error instanceof Error ? error.message : "Refresh failed",
      );
      await load();
    } finally {
      setRefreshing(false);
    }
  };

  const health = data?.health;
  const now = health ? new Date(health.server_time).getTime() : 0;
  const stale =
    !!health?.market_open &&
    !!health.data_as_of &&
    now - new Date(health.data_as_of).getTime() > STALE_MS;

  const pageTitle =
    tab === "lab" && route
      ? route.mode === "edit"
        ? "Edit strategy"
        : "New strategy"
      : tab === "lab" && path[0] === "backtest"
        ? "Strategy backtest"
        : tab === "lab" && path[0] === "tune"
          ? "Strategy tuner"
          : tab === "market" && strikeRoute
            ? "Strike chart"
            : TABS.find((item) => item.id === tab)?.label;
  // Sub-pages (the editor, a backtest, a strike chart) carry their own heading.
  const topLevel = !(
    (tab === "lab" && (route || path[0] === "backtest")) ||
    (tab === "market" && strikeRoute)
  );

  return (
    <AssistantProvider>
      <div
        className={`shell ${collapsed ? "sidebar-collapsed" : ""} ${menuOpen ? "menu-open" : ""}`}
      >
        <aside className="sidebar">
          <div className="brand">
            <span className="brand-mark" aria-hidden />
            <span className="sidebar-text">AlgoVerse</span>
          </div>
          <nav aria-label="Sections">
            {NAV_GROUPS.map((group) => (
              <div className="nav-group" key={group.title}>
                <div className="nav-title sidebar-text">{group.title}</div>
                {group.items.map((item) => (
                  <a
                    key={item.id}
                    href={`#${item.id}`}
                    className={tab === item.id ? "active" : ""}
                    aria-current={tab === item.id ? "page" : undefined}
                    title={collapsed ? item.label : undefined}
                    onClick={() => setMenuOpen(false)}
                  >
                    <NavIcon path={item.icon} />
                    <span className="sidebar-text">{item.label}</span>
                  </a>
                ))}
              </div>
            ))}
          </nav>
          <div className="sidebar-foot">
            <p className="sidebar-note">
              For personal analysis, not investment advice. Scores are model
              estimates. No real orders can be placed from this app.
            </p>
            <div className="sidebar-foot-row">
              <span className="paper-tag sidebar-text">Paper trading</span>
              <button
                type="button"
                className="sidebar-toggle"
                onClick={() => setCollapsed((value) => !value)}
                aria-expanded={!collapsed}
                aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              >
                <NavIcon path={collapsed ? "M9 6l6 6-6 6" : "M15 6l-6 6 6 6"} />
              </button>
            </div>
          </div>
        </aside>
        <button
          type="button"
          className="menu-backdrop"
          aria-label="Close menu"
          onClick={() => setMenuOpen(false)}
        />
        <div className="app">
          <header className="topbar">
            <button
              type="button"
              className="menu-button"
              aria-label="Open menu"
              onClick={() => setMenuOpen(true)}
            >
              <NavIcon path="M4 6h16M4 12h16M4 18h16" />
            </button>
            <MarketTape />
            <div className="topbar-status">
              {health && (
                <>
                  <span
                    className="connection"
                    title={
                      health.broker_session_valid
                        ? "Upstox connected"
                        : "Upstox logged out"
                    }
                  >
                    <span
                      className={`connection-dot ${
                        !health.broker_session_valid
                          ? "off"
                          : health.market_open
                            ? "live"
                            : ""
                      }`}
                      aria-hidden
                    />
                    <span className="connection-label">
                      {health.market_open ? "Market open" : "Market closed"}
                      <span className="muted">
                        {" "}
                        · data {age(health.data_as_of, now)}
                      </span>
                      {!health.broker_session_valid && (
                        <span className="muted"> · Upstox logged out</span>
                      )}
                    </span>
                  </span>
                </>
              )}
              <button
                className="button ghost small"
                onClick={refresh}
                disabled={refreshing || !health?.broker_session_valid}
                title={
                  health?.broker_session_valid
                    ? "Rank ideas from fresh quotes"
                    : "Log in to Upstox first"
                }
              >
                {refreshing ? "Refreshing…" : "Refresh"}
              </button>
              <button
                className="icon-button"
                onClick={toggleTheme}
                aria-label="Switch colour theme"
                title="Switch colour theme"
              >
                <NavIcon path="M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z" />
              </button>
            </div>
          </header>

          {loadError && (
            <div className="banner banner-critical">
              <Status tone="critical">Server unavailable</Status> {loadError}
            </div>
          )}
          {health && !health.upstox_configured && (
            <div className="banner banner-warning">
              <Status tone="warning">Setup needed</Status> Add your Upstox API
              key and secret to <code>.env</code>, then restart the server.
            </div>
          )}
          {health?.upstox_configured && !health.broker_session_valid && (
            <div className="banner banner-warning">
              <Status tone="warning">Login needed</Status> Upstox sessions end
              at 03:30 each day.
              <a className="button" href="/auth/login">
                Log in to Upstox
              </a>
            </div>
          )}
          {stale && (
            <div className="banner banner-warning">
              <Status tone="warning">Stale data</Status> The market is open but
              data is {age(health!.data_as_of, now)}. Check that{" "}
              <code>algoverse collect</code> is running.
            </div>
          )}
          {refreshError && (
            <div className="banner banner-critical">
              <Status tone="critical">Refresh failed</Status> {refreshError}
            </div>
          )}

          <main>
            {topLevel && (
              <div className="page-head">
                <div>
                  <h1 className="page-title">{pageTitle}</h1>
                  <p className="page-sub">
                    {tab === "lab" && path[0] === "tune"
                      ? "Change one setting at a time, and keep only what holds up on unseen sessions."
                      : PAGE_SUBS[tab]}
                  </p>
                </div>
              </div>
            )}
            {!data &&
              !loadError &&
              tab !== "overview" &&
              tab !== "history" &&
              !(tab === "market" && (shownMarket || strikeRoute)) && (
                <Empty title="Loading…" />
              )}
            {/* The overview loads its own data and can show this browser's saved copy offline. */}
            {tab === "overview" && <OverviewView />}
            {tab === "history" && <TestHistoryView />}
            {data && tab === "ideas" && <IdeasView ideas={data.ideas.ideas} />}
            {/* Options also falls back to the chains this browser saved, so it works offline. */}
            {tab === "market" && strikeRoute && (
              <StrikePage route={strikeRoute} market={shownMarket} />
            )}
            {tab === "market" && !strikeRoute && shownMarket && (
              <MarketView market={shownMarket} offline={!data && !!loadError} />
            )}
            {data && tab === "paper" && (
              <PaperView portfolio={data.portfolio} />
            )}
            {data && tab === "risk" && <RiskView portfolio={data.portfolio} />}
            {data && tab === "lab" && route && (
              <StrategyEditor key={path.join("/")} route={route} />
            )}
            {tab === "lab" && path[0] === "backtest" && path[1] && (
              <LabBacktestView id={path[1]} />
            )}
            {tab === "lab" && path[0] === "tune" && (
              <TunerView
                key={path.join("/")}
                sessionId={path[1] === "s" && path[2] ? path[2] : null}
                startId={path[1] === "from" && path[2] ? path[2] : null}
              />
            )}
            {data &&
              tab === "lab" &&
              !route &&
              path[0] !== "backtest" &&
              path[0] !== "tune" && (
                <LabView
                  key={path.join("/")}
                  initialSelected={
                    path[0] === "view" && path[1] ? path[1] : null
                  }
                />
              )}
            {data && tab === "backtests" && (
              <BacktestsView listing={data.backtests} />
            )}
            {data && tab === "forward" && (
              <ForwardView overview={data.forward} />
            )}
          </main>
        </div>
      </div>
    </AssistantProvider>
  );
}

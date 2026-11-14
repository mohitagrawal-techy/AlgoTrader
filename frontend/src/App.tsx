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
import { CommandPalette, type Command } from "./shell/CommandPalette";
import { StatusBar } from "./shell/StatusBar";
import {
  ChevronLeft,
  ChevronRight,
  Command as CommandIcon,
  FileText,
  FlaskConical,
  History,
  LayoutDashboard,
  Lightbulb,
  ListChecks,
  Menu,
  Radio,
  RefreshCw,
  ShieldAlert,
  Table2,
  Microscope,
  CalendarDays,
  Database,
} from "lucide-react";
import { Alert, Button, ButtonLink, Empty } from "./components/ui";
import { AssistantProvider } from "./assistant";
import { BacktestsView } from "./views/Backtests";
import { ForwardView } from "./views/Forward";
import { IdeasView } from "./views/Ideas";
import { LabView } from "./views/Lab";
import { TestHistoryView } from "./views/TestHistory";
import { LabBacktestView } from "./views/LabBacktest";
import { MarketView, readSavedMarket, saveMarket } from "./views/Market";
import { CommandCenter } from "./desk/CommandCenter";
import { PaperView } from "./views/Paper";
import { RiskView } from "./views/Risk";
import { StrategyEditor, type EditorRoute } from "./builder/StrategyBuilder";
import { readStrikeRoute, StrikePage } from "./views/StrikeChart";
import { TunerView } from "./views/Tuner";
import { ResearchPage } from "./components/research/ResearchPage";
import { CalendarPage } from "./components/calendar/CalendarPage";
import { DataPage } from "./components/data/DataPage";

const NAV_GROUPS = [
  {
    title: "Market",
    items: [
      {
        id: "overview",
        label: "Command center",
        icon: LayoutDashboard,
      },
      {
        id: "market",
        label: "Options",
        icon: Table2,
      },
      {
        id: "ideas",
        label: "Ideas",
        icon: Lightbulb,
      },
      {
        id: "calendar",
        label: "Calendar",
        icon: CalendarDays,
      },
      {
        id: "data",
        label: "Data",
        icon: Database,
      },
    ],
  },
  {
    title: "Strategies",
    items: [
      {
        id: "lab",
        label: "Strategy lab",
        icon: FlaskConical,
      },
      {
        id: "research",
        label: "Research",
        icon: Microscope,
      },
      {
        id: "backtests",
        label: "Backtests",
        icon: History,
      },
      {
        id: "history",
        label: "Test history",
        icon: ListChecks,
      },
      { id: "forward", label: "Forward test", icon: Radio },
    ],
  },
  {
    title: "Portfolio",
    items: [
      {
        id: "paper",
        label: "Paper trades",
        icon: FileText,
      },
      {
        id: "risk",
        label: "Risk",
        icon: ShieldAlert,
      },
    ],
  },
] as const;
const TABS = NAV_GROUPS.flatMap((group) => [...group.items]);
type TabId = (typeof NAV_GROUPS)[number]["items"][number]["id"];
const SIDEBAR_KEY = "algoverse-sidebar";

const PAGE_SUBS: Record<string, string> = {
  overview: "The paper desk as it stands, and the market around it.",
  market: "Chain analytics for one underlying and expiry.",
  ideas: "Trades ranked from the latest option chains.",
  calendar:
    "Events, holidays and expiries, and how far the event list has been checked.",
  data: "What market data is stored, and loaders that fetch more from Upstox.",
  lab: "Build your own strategies, then test and tune them.",
  research:
    "How the indices behave: time of day, volatility, correlations, gaps, pairs and an ML signal.",
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
  const [latencyMs, setLatencyMs] = useState<number | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
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
      const started = performance.now();
      const [health, ideas, market, portfolio, backtests, forward] =
        await Promise.all([
          api.health().then((answer) => {
            setLatencyMs(performance.now() - started);
            return answer;
          }),
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

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
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

  const go = (hash: string) => () => {
    window.location.hash = hash;
  };
  const commands: Command[] = [
    ...TABS.map((item) => ({
      id: `page-${item.id}`,
      label: item.label,
      group: "Page",
      run: go(item.id),
    })),
    {
      id: "new-strategy",
      label: "New strategy",
      group: "Action",
      run: go("lab/new"),
    },
    {
      id: "tune",
      label: "Tune a strategy",
      group: "Action",
      run: go("lab/tune"),
    },
    {
      id: "refresh",
      label: "Refresh ideas from fresh quotes",
      group: "Action",
      run: () => void refresh(),
    },
    {
      id: "sidebar",
      label: collapsed ? "Expand the sidebar" : "Collapse the sidebar",
      group: "Action",
      run: () => setCollapsed((value) => !value),
    },
  ];

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
                    <item.icon aria-hidden size={17} strokeWidth={1.8} />
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
                {collapsed ? (
                  <ChevronRight aria-hidden size={16} />
                ) : (
                  <ChevronLeft aria-hidden size={16} />
                )}
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
              <Menu aria-hidden size={18} />
            </button>
            <MarketTape />
            <div className="topbar-status">
              <button
                type="button"
                className="palette-button"
                onClick={() => setPaletteOpen(true)}
                aria-label="Go to a page or action"
                aria-keyshortcuts="Meta+K Control+K"
              >
                <CommandIcon aria-hidden size={13} />
                <span>K</span>
              </button>
              <Button
                icon={RefreshCw}
                onClick={refresh}
                disabled={refreshing || !health?.broker_session_valid}
                title={
                  health?.broker_session_valid
                    ? "Rank ideas from fresh quotes"
                    : "Log in to Upstox first"
                }
              >
                {refreshing ? "Refreshing…" : "Refresh"}
              </Button>
            </div>
          </header>

          {loadError && (
            <Alert
              tone="critical"
              title="Server unavailable"
              className="page-alert"
            >
              {loadError}
            </Alert>
          )}
          {health && !health.upstox_configured && (
            <Alert tone="warning" title="Setup needed" className="page-alert">
              Add your Upstox API key and secret to <code>.env</code>, then
              restart the server.
            </Alert>
          )}
          {health?.upstox_configured && !health.broker_session_valid && (
            <Alert
              tone="warning"
              title="Login needed"
              className="page-alert"
              action={
                <ButtonLink href="/auth/login" variant="primary">
                  Log in to Upstox
                </ButtonLink>
              }
            >
              Upstox sessions end at 03:30 each day.
            </Alert>
          )}
          {stale && (
            <Alert tone="warning" title="Stale data" className="page-alert">
              The market is open but data is {age(health!.data_as_of, now)}.
              Check that <code>algoverse collect</code> is running.
            </Alert>
          )}
          {refreshError && (
            <Alert
              tone="critical"
              title="Refresh failed"
              className="page-alert"
            >
              {refreshError}
            </Alert>
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
              tab !== "research" &&
              tab !== "calendar" &&
              tab !== "data" &&
              !(tab === "market" && (shownMarket || strikeRoute)) && (
                <Empty title="Loading…" />
              )}
            {/* The overview loads its own data and can show this browser's saved copy offline. */}
            {tab === "overview" && (
              <CommandCenter
                portfolio={data?.portfolio}
                forward={data?.forward}
                market={shownMarket}
              />
            )}
            {tab === "history" && <TestHistoryView />}
            {tab === "research" && <ResearchPage />}
            {tab === "calendar" && <CalendarPage />}
            {tab === "data" && <DataPage />}
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
          <StatusBar
            health={health}
            offline={!data && !!loadError}
            latencyMs={latencyMs}
          />
        </div>
      </div>
      {paletteOpen && (
        <CommandPalette
          commands={commands}
          onClose={() => setPaletteOpen(false)}
        />
      )}
    </AssistantProvider>
  );
}

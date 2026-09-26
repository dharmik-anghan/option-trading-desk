import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./App.css";
import {
  WATCHLIST,
  getBaskets,
  getHealth,
  getOptionChain,
  getPortfolio,
  getPortfolioHistory,
  getQuotes,
  getMarketContext,
  getEvents,
  getNews,
  describeError,
} from "./api";
import { useLive, useNow } from "./useLive";
import { Toolbar } from "./components/Toolbar";
import { MarketWatch } from "./components/MarketWatch";
import { NewsPanel } from "./components/NewsPanel";
import { PositionsRail } from "./components/PositionsRail";
import { OptionChainPanel } from "./components/OptionChainPanel";
import { BasketsPanel } from "./components/BasketsPanel";
import { AlertsPanel } from "./components/AlertsPanel";
import { DEFAULT_LIMITS, affectsIndia, dedupeLog, evaluate, reconcile } from "./alerts";
import type { Alert, Limits } from "./alerts";

type View = "trading" | "oi" | "greeks";
type Theme = "dark" | "light";

// Intervals, and a stagger so the panels do not all call at the same instant.
// The broker allows about 10 requests a second and 200 a minute; the earlier
// 5s/5s/15s set with no stagger produced 44 requests a minute in bursts of
// 15-20, which returned `429 request limit reached`. Reads are also cached
// server-side (broker/cache.py), so these are an upper bound, not a floor.
const PORTFOLIO_MS = 8000;
const QUOTES_MS = 8000;
const CHAIN_MS = 12000;
const CONTEXT_MS = 15000;
// Both are somebody else's website behind a server-side cache, so polling them
// hard buys nothing - the backend would just hand back the same snapshot.
const NEWS_MS = 120000;
const EVENTS_MS = 600000;
const BASKETS_MS = 30000;
const HEALTH_MS = 30000;

function stored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? ({ ...fallback, ...JSON.parse(raw) } as T) : fallback;
  } catch {
    return fallback;
  }
}

interface SavedAlerts {
  log: Alert[];
  /** Keys of the conditions that were true when this was written. */
  active: string[];
}

function persistAlerts(log: readonly Alert[], active: ReadonlySet<string>): void {
  try {
    localStorage.setItem(
      "optiondesk-alerts",
      JSON.stringify({ log: log.slice(0, 200), active: [...active] } satisfies SavedAlerts),
    );
  } catch {
    // the log is a convenience; losing it must not break the desk
  }
}

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem("optiondesk-theme");
    if (saved === "dark" || saved === "light") return saved;
  } catch {
    // storage can throw in a private window; fall through to the media query
  }
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export default function App() {
  const [symbol, setSymbol] = useState("NSE:NIFTY50-INDEX");
  const [view, setView] = useState<View>("trading");
  // "" means whichever expiry the broker considers nearest
  const [expiry, setExpiry] = useState("");
  // strikes either side of the money; Fyers serves at least 50
  const [depth, setDepth] = useState(15);
  // the chain is reference material here, not the main view, so it starts shut
  const [chainOpen, setChainOpen] = useState(false);
  const [limits, setLimits] = useState<Limits>(() => stored("optiondesk-limits", DEFAULT_LIMITS));
  // The log AND the set of conditions that were already true are restored
  // together. Restoring only the log makes every still-true condition look
  // like a fresh transition on reload, so a refresh re-fired all of them.
  const [saved] = useState(() => {
    const raw = stored<SavedAlerts>("optiondesk-alerts", { log: [], active: [] });
    // Heal repeats an earlier build wrote: the log outlives the code, so a fix
    // alone does not remove what the bug already recorded.
    return { ...raw, log: dedupeLog(raw.log ?? []) };
  });
  const [alertLog, setAlertLog] = useState<Alert[]>(saved.log);
  const activeKeys = useRef<Set<string>>(new Set(saved.active));
  const logRef = useRef<Alert[]>(saved.log);
  const [paused, setPaused] = useState(false);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [basketNonce, setBasketNonce] = useState(0);

  useEffect(() => {
    try {
      localStorage.setItem("optiondesk-limits", JSON.stringify(limits));
    } catch {
      // a limit we cannot remember is still enforced this session
    }
  }, [limits]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("optiondesk-theme", theme);
    } catch {
      // not being able to remember the theme is not worth failing over
    }
  }, [theme]);

  const health = useLive(getHealth, HEALTH_MS, [], paused, 0);
  const portfolio = useLive(getPortfolio, PORTFOLIO_MS, [], paused, 150);
  const quotes = useLive(() => getQuotes(WATCHLIST), QUOTES_MS, [], paused, 600);
  // no request at all while the chain is hidden
  const chain = useLive(
    () => getOptionChain(symbol, depth, expiry),
    CHAIN_MS,
    [symbol, expiry, depth, chainOpen],
    paused || !chainOpen,
    1050,
  );
  const baskets = useLive(() => getBaskets(true), BASKETS_MS, [basketNonce], paused, 300);
  const context = useLive(() => getMarketContext(symbol), CONTEXT_MS, [symbol], paused, 850);
  const news = useLive(() => getNews(40), NEWS_MS, [], paused, 1900);
  const events = useLive(() => getEvents(45, "HM"), EVENTS_MS, [], paused, 2400);
  const history = useLive(() => getPortfolioHistory(7), BASKETS_MS, [basketNonce], paused, 1500);

  const now = useNow(!paused);
  const lastAt = useMemo(
    () => Math.max(portfolio.at ?? 0, quotes.at ?? 0),
    [portfolio.at, quotes.at],
  );
  const agoSeconds = lastAt ? Math.max(0, Math.round((now - lastAt) / 1000)) : null;

  // One line for the toolbar, from whichever panel is unhappiest. A blocking
  // failure outranks a transient one; the backend being down outranks both.
  const trouble = useMemo(() => {
    const candidates = [
      health.error,
      portfolio.error,
      quotes.error,
      baskets.error,
      context.error,
      chain.error,
    ]
      .map(describeError)
      .filter((d): d is { text: string; transient: boolean } => d !== null);
    if (!candidates.length) return null;
    return candidates.find((c) => !c.transient) ?? candidates[0];
  }, [health.error, portfolio.error, quotes.error, baskets.error, context.error, chain.error]);

  // The soonest release that bears on an Indian index. Shown in the toolbar
  // because it is the one piece of context there that is not derivable from
  // the prices beside it.
  const nextEvent = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);
    return (events.data?.events ?? []).find((e) => affectsIndia(e) && e.day >= today) ?? null;
  }, [events.data]);

  // Expiry dates of the structures you hold, so the calendar can mark an event
  // that lands before one of them.
  const expiryDays = useMemo(() => {
    const out = new Set<string>();
    for (const b of baskets.data ?? []) {
      if (b.expiry_date && b.legs.some((l) => l.is_open)) {
        // the calendar dates are ISO; the basket's is DD-MM-YYYY
        const [d, m, y] = b.expiry_date.split("-");
        if (d && m && y) out.add(`${y}-${m}-${d}`);
      }
    }
    return [...out];
  }, [baskets.data]);

  // A rate limit still leaves good figures on screen, so a red bar across every
  // panel would be contradicting them. The toolbar carries it instead; panels
  // only speak up when they have genuinely got nothing to show.
  const blockingOnly = (e: Error | null): Error | null =>
    e && !describeError(e)?.transient ? e : null;
  const stale = agoSeconds !== null && agoSeconds > PORTFOLIO_MS / 1000 + 8;

  // quotes cover every symbol and always poll; the chain is only open sometimes
  const spot =
    quotes.data?.[symbol]?.ltp ?? context.data?.spot ?? chain.data?.underlying_ltp ?? null;
  const quote = quotes.data?.[symbol];
  const dayChange = quote ? quote.ltp - quote.prev_close : (context.data?.change ?? null);
  const dayChangePct = quote
    ? quote.prev_close > 0
      ? (quote.ltp - quote.prev_close) / quote.prev_close
      : null
    : (context.data?.change_pct ?? null);

  // an expiry token belongs to one underlying, so drop it when that changes
  const pickSymbol = useCallback((next: string) => {
    setSymbol(next);
    setExpiry("");
  }, []);

  // Re-evaluated whenever the data it reads changes, which is what the polls
  // already provide - no second clock to keep in step.
  // Which conditions hold right now. Derived during render rather than kept in
  // state, because it is a pure function of data already on screen.
  const conditions = useMemo(
    () =>
      paused ? [] : evaluate(portfolio.data, baskets.data, limits, events.data?.events ?? []),
    [paused, portfolio.data, baskets.data, limits, events.data],
  );

  // Nothing fetched yet is not the same as nothing wrong: evaluating early
  // reports no conditions, which clears the restored active set and re-fires
  // everything once the data lands. That was the refresh duplicating alerts.
  //
  // Both sources, not either. The portfolio answers in one broker call while
  // the live basket list makes several, so `||` still let a pass run with the
  // baskets missing - and every basket-derived alert fired a second time.
  // An empty list is loaded; only null is "not yet".
  const hasData = portfolio.data !== null && baskets.data !== null;
  const eventsLoaded = events.data !== null;

  useEffect(() => {
    if (paused || !hasData) return;

    // Deliberately not inside a setAlertLog updater: advancing `activeKeys` is
    // a side effect, and React may call an updater more than once. It did -
    // the second call saw every key already active, fired nothing, and handed
    // back the previous log, so alerts silently never appeared.
    // Event conditions cannot be judged until the calendar has loaded, and it
    // polls far more slowly than the rest. Without this its keys are dropped
    // in the gap and re-fire on arrival - the duplicate event alert.
    const evaluable = (key: string) => (key.startsWith("event:") ? eventsLoaded : true);
    const next = reconcile(
      activeKeys.current,
      logRef.current,
      conditions,
      Date.now(),
      undefined,
      evaluable,
    );
    activeKeys.current = next.active;
    if (next.fired.length) {
      logRef.current = next.log;
      setAlertLog(next.log);
    }
    // saved every pass, not only when something fires: a condition that has
    // *cleared* has to be recorded too, or it would fire again after a reload
    persistAlerts(logRef.current, activeKeys.current);
  }, [conditions, paused, hasData, eventsLoaded]);

  const onBasketChanged = useCallback(() => {
    setBasketNonce((n) => n + 1);
    portfolio.refresh();
  }, [portfolio]);

  return (
    <div className="ws">
      <Toolbar
        symbol={symbol}
        spot={spot}
        change={dayChange}
        changePct={dayChangePct}
        nextEvent={nextEvent}
        portfolio={portfolio.data}
        health={health.data}
        trouble={trouble}
        stale={stale}
        agoSeconds={agoSeconds}
        paused={paused}
        onPause={() => setPaused((p) => !p)}
        theme={theme}
        onTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
        context={context.data}
      />

      <div id="left">
        <MarketWatch
          symbol={symbol}
          onSymbol={pickSymbol}
          quotes={quotes.data}
          error={blockingOnly(quotes.error)}
        />
        <PositionsRail
          portfolio={portfolio.data}
          portfolioError={blockingOnly(portfolio.error)}
          history={history.data}
        />
        <NewsPanel news={news.data} events={events.data} expiryDays={expiryDays} />
      </div>

      <AlertsPanel
        alerts={alertLog}
        activeCount={conditions.length}
        limits={limits}
        onLimits={setLimits}
        onClear={() => {
          // the active set is kept, so clearing the log does not re-fire
          // everything that is still true on the next tick
          logRef.current = [];
          setAlertLog([]);
          persistAlerts([], activeKeys.current);
        }}
      />

      <OptionChainPanel
        chain={chain.data}
        error={blockingOnly(chain.error)}
        loading={chain.loading}
        view={view}
        onView={setView}
        positions={portfolio.data?.positions ?? []}
        open={chainOpen}
        onOpen={setChainOpen}
        expiry={expiry}
        onExpiry={setExpiry}
        depth={depth}
        onDepth={setDepth}
      />

      <BasketsPanel
        baskets={baskets.data}
        error={blockingOnly(baskets.error)}
        loading={baskets.loading}
        positions={portfolio.data?.positions ?? []}
        symbol={symbol}
        quotes={quotes.data}
        onChanged={onBasketChanged}
      />

    </div>
  );
}

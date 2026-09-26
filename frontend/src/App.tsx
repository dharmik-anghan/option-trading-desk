import { useCallback, useEffect, useMemo, useState } from "react";
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
import { PerpsChart } from "./components/PerpsChart";
import { PerpsPositions } from "./components/PerpsPositions";
import { PerpsWatch } from "./components/PerpsWatch";
import {
  UNDERLYINGS,
  addWatch,
  affectsIndia,
  clearAlerts,
  deleteWatch,
  getAlerts,
  getPerpsDesk,
  getVenues,
  setWatchEnabled,
} from "./api";
import type { NewWatch } from "./api";

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
// The backend judges about once a minute, so polling faster only repeats an
// answer. Slower than that and a fired alert sits unseen on screen.
const ALERTS_MS = 20000;
// Faster than the rest: this one reads prices the stream already delivered, so a
// poll costs the backend a dictionary lookup rather than a venue request.
const PERPS_MS = 2000;

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
  const [paused, setPaused] = useState(false);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [basketNonce, setBasketNonce] = useState(0);
  // Which desk is on screen. Remembered, because it is the frame everything else
  // is read in and having it reset on reload is a small daily annoyance.
  const [venueId, setVenueId] = useState<string>(() => {
    try {
      return localStorage.getItem("optiondesk-venue") ?? "fyers";
    } catch {
      return "fyers";
    }
  });
  const [perpSymbol, setPerpSymbol] = useState("BTCUSDT");
  const onPerps = venueId !== "fyers";
  // Which news topics are showing. Null means "follow the desk", which is what
  // anyone wants until they say otherwise; an explicit choice is kept across a
  // desk switch, because having the desk overrule a filter you just set would
  // make the chips feel broken.
  const [newsTopics, setNewsTopics] = useState<string[] | null>(null);
  // A failed write, shown rather than only logged. Not seeing why a threshold
  // would not save is most of what makes it feel broken.
  const [alertSaveError, setAlertSaveError] = useState<string | null>(null);
  const shownTopics = newsTopics ?? (onPerps ? ["crypto", "commodities"] : ["india"]);

  useEffect(() => {
    try {
      localStorage.setItem("optiondesk-venue", venueId);
    } catch {
      // forgetting which desk was open is not worth failing over
    }
  }, [venueId]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("optiondesk-theme", theme);
    } catch {
      // not being able to remember the theme is not worth failing over
    }
  }, [theme]);

  const health = useLive(getHealth, HEALTH_MS, [], paused, 0);
  const venues = useLive(getVenues, 0, [], paused, 50);
  const portfolio = useLive(getPortfolio, PORTFOLIO_MS, [], paused, 150);
  // The options polls stop while the perpetuals desk is open, and vice versa.
  // Two desks' worth of requests for one desk on screen is how a rate limit gets
  // spent on panels nobody is looking at.
  const quotes = useLive(
    () => getQuotes(WATCHLIST),
    QUOTES_MS,
    [],
    paused || onPerps,
    600,
  );
  // Cheap: it reads the price hub in memory rather than calling the venue, which
  // is the point of the stream.
  const perps = useLive(getPerpsDesk, PERPS_MS, [], paused || !onPerps, 100);
  // no request at all while the chain is hidden
  const chain = useLive(
    () => getOptionChain(symbol, depth, expiry),
    CHAIN_MS,
    [symbol, expiry, depth, chainOpen],
    paused || !chainOpen,
    1050,
  );
  const baskets = useLive(() => getBaskets(true), BASKETS_MS, [basketNonce], paused || onPerps, 300);
  const context = useLive(
    () => getMarketContext(symbol),
    CONTEXT_MS,
    [symbol],
    paused || onPerps,
    850,
  );
  const news = useLive(
    () => getNews(40, shownTopics),
    NEWS_MS,
    [shownTopics.join(",")],
    paused,
    1900,
  );
  const events = useLive(() => getEvents(45, "HM"), EVENTS_MS, [], paused, 2400);
  const history = useLive(
    () => getPortfolioHistory(7),
    BASKETS_MS,
    [basketNonce],
    paused || onPerps,
    1500,
  );

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

  // Alerts are read, not computed. The engine that raises them runs in the
  // backend (`alerting/`), which is what lets them fire with this tab closed and
  // reach a phone - neither of which a browser engine could ever do. It also
  // means the log is the same in every browser rather than per-origin.
  const alerts = useLive(() => getAlerts(), paused ? 0 : ALERTS_MS, [], paused, 2500);

  const refreshAlerts = alerts.refresh;
  const noteAlertFailure = useCallback((error: unknown) => {
    setAlertSaveError(error instanceof Error ? error.message : String(error));
  }, []);
  const afterAlertWrite = useCallback(() => {
    setAlertSaveError(null);
    refreshAlerts();
  }, [refreshAlerts]);
  const onClear = useCallback(() => {
    void clearAlerts().then(afterAlertWrite).catch(noteAlertFailure);
  }, [afterAlertWrite, noteAlertFailure]);
  const onAddWatch = useCallback(
    (watch: NewWatch) => {
      void addWatch(watch).then(afterAlertWrite).catch(noteAlertFailure);
    },
    [afterAlertWrite, noteAlertFailure],
  );
  const onToggleWatch = useCallback(
    (id: number, enabled: boolean) => {
      void setWatchEnabled(id, enabled).then(afterAlertWrite).catch(noteAlertFailure);
    },
    [afterAlertWrite, noteAlertFailure],
  );
  const onDeleteWatch = useCallback(
    (id: number) => {
      void deleteWatch(id).then(afterAlertWrite).catch(noteAlertFailure);
    },
    [afterAlertWrite, noteAlertFailure],
  );

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
        venues={venues.data ?? []}
        venueId={venueId}
        onVenue={setVenueId}
        perps={onPerps ? (perps.data ?? null) : null}
      />

      <div id="left">
        {onPerps ? (
          <PerpsWatch
            instruments={perps.data?.instruments ?? []}
            prices={perps.data?.prices ?? []}
            selected={perpSymbol}
            onSelect={setPerpSymbol}
            quoteAsset={perps.data?.quote_currency ?? "USDT"}
          />
        ) : (
          <MarketWatch
            symbol={symbol}
            onSymbol={pickSymbol}
            quotes={quotes.data}
            error={blockingOnly(quotes.error)}
          />
        )}
        {onPerps && (
          <PerpsPositions
            positions={perps.data?.positions ?? []}
            error={perps.data?.positions_error ?? null}
            quoteCurrency={perps.data?.quote_currency ?? "USDT"}
            moneyCurrency={perps.data?.money_currency ?? "INR"}
            onChanged={perps.refresh}
          />
        )}
        {!onPerps && (
          <PositionsRail
            portfolio={portfolio.data}
            portfolioError={blockingOnly(portfolio.error)}
            history={history.data}
          />
        )}
        {/* The calendar is shared: a release moves an index and a gold
            perpetual alike, so both desks read one feed. */}
        <NewsPanel
          news={news.data}
          events={events.data}
          expiryDays={expiryDays}
          topics={shownTopics}
          onTopics={setNewsTopics}
        />
      </div>

      <AlertsPanel
        alerts={alerts.data?.alerts ?? []}
        activeCount={alerts.data?.active.length ?? 0}
        watches={alerts.data?.watches ?? []}
        symbols={
          onPerps
            ? (perps.data?.instruments ?? []).map((i) => ({ id: i.symbol, name: i.name }))
            : UNDERLYINGS
        }
        telegram={alerts.data?.watcher.telegram ?? false}
        watching={alerts.data?.watcher.running ?? false}
        saveError={alertSaveError}
        trouble={alerts.data?.watcher.last_error ?? null}
        onClear={onClear}
        onAddWatch={onAddWatch}
        onToggleWatch={onToggleWatch}
        onDeleteWatch={onDeleteWatch}
      />

      {onPerps ? (
        <PerpsChart
          desk={perps.data}
          selected={perpSymbol}
          last={perps.data?.prices.find((p) => p.symbol === perpSymbol)?.price ?? null}
        />
      ) : (
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
      )}

      {!onPerps && (
        <BasketsPanel
          baskets={baskets.data}
          error={blockingOnly(baskets.error)}
          loading={baskets.loading}
          positions={portfolio.data?.positions ?? []}
          symbol={symbol}
          quotes={quotes.data}
          onChanged={onBasketChanged}
        />
      )}

    </div>
  );
}

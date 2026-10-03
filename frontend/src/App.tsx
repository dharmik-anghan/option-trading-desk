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
  getVolatility,
  getEvents,
  getNews,
  describeError,
} from "./api";
import type { Theme } from "./hooks/useTheme";
import { useLive, useNow } from "./hooks/useLive";
import { useAccountEvents } from "./hooks/useAccountEvents";
import { useTickStream } from "./hooks/useTickStream";
import { liveBaskets, liveChain, livePortfolio, watchedSymbols } from "./live";
import { Toolbar } from "./components/Toolbar";
import { MarketWatch } from "./components/MarketWatch";
import { NewsPanel } from "./components/NewsPanel";
import { PositionsRail } from "./components/PositionsRail";
import { IndexChart } from "./components/IndexChart";
import { VolPanel } from "./components/VolPanel";
import { OptionChainPanel } from "./components/OptionChainPanel";
import { BasketsPanel } from "./components/BasketsPanel";
import { AlertsPanel } from "./components/AlertsPanel";
import { AlertsBell } from "./components/AlertsBell";
import { PerpsChart } from "./components/PerpsChart";
import { PerpsFunds } from "./components/PerpsFunds";
import { PerpsPositions } from "./components/PerpsPositions";
import { PerpsTicket } from "./components/PerpsTicket";
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

// Intervals, and a stagger so the panels do not all call at the same instant.
// The broker allows about 10 requests a second and 200 a minute; the earlier
// 5s/5s/15s set with no stagger produced 44 requests a minute in bursts of
// 15-20, which returned `429 request limit reached`. Reads are also cached
// server-side (broker/cache.py), so these are an upper bound, not a floor.
const PORTFOLIO_MS = 8000;
/** The portfolio poll while the account socket is up: a backstop, not the source. */
const ACCOUNT_BACKUP_MS = 60000;
const QUOTES_MS = 8000;
const CHAIN_MS = 12000;
const CONTEXT_MS = 60000;
// Both are somebody else's website behind a server-side cache, so polling them
// hard buys nothing - the backend would just hand back the same snapshot.
const NEWS_MS = 120000;
const EVENTS_MS = 600000;
const BASKETS_MS = 30000;
const HEALTH_MS = 30000;
// The backend judges about once a minute, so polling faster only repeats an
// answer. Slower than that and a fired alert sits unseen on screen.
const ALERTS_MS = 20000;
// Implied volatility is a statement about a session, and every pass is a chain
// fetch. Once a minute is far more often than the number changes meaningfully.
const VOL_MS = 60000;
// Faster than the rest: this one reads prices the stream already delivered, so a
// poll costs the backend a dictionary lookup rather than a venue request.
// Slow, because prices arrive on their own now. What is left here changes on the
// scale of an order being placed, not a tick.
const PERPS_MS = 15000;

interface Props {
  /** Which venue this desk trades. "fyers" is the options desk; anything else
      is a perpetuals desk, which has different panels. */
  venueId: string;
  onVenue: (id: string) => void;
  onHome: () => void;
  theme: Theme;
  onTheme: () => void;
}

export default function App({ venueId, onVenue, onHome, theme, onTheme }: Props) {
  const [symbol, setSymbol] = useState("NSE:NIFTY50-INDEX");
  const [view, setView] = useState<View>("trading");
  // "" means whichever expiry the broker considers nearest
  const [expiry, setExpiry] = useState("");
  // strikes either side of the money; Fyers serves at least 50
  const [depth, setDepth] = useState(15);
  // the chain is reference material here, not the main view, so it starts shut
  const [chainOpen, setChainOpen] = useState(false);
  const [paused, setPaused] = useState(false);
  const [basketNonce, setBasketNonce] = useState(0);
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
  // The log is a second copy of what Telegram already delivered, so it lives
  // behind a count in the header rather than in a column of its own.
  const [alertsOpen, setAlertsOpen] = useState(false);
  const shownTopics = newsTopics ?? (onPerps ? ["crypto", "commodities"] : ["india"]);

  const health = useLive(getHealth, HEALTH_MS, [], paused, 0);
  const venues = useLive(getVenues, 0, [], paused, 50);
  // Whether the NSE is trading. Nothing on the options desk can change while it
  // is not - a Saturday, a holiday, the night - so every options poll below
  // loads once and stops, and starts again by itself at the bell (health is
  // what notices, and it never calls the broker). Assumed open until health has
  // said, so a first load is not held back.
  const nseOpen = health.data?.nse?.open ?? true;
  const every = (ms: number) => (nseOpen ? ms : 0);
  // The account, pushed: while the broker's account socket is up, positions are
  // read when it says they changed, and polled only as a slow backstop.
  // Refetched in place rather than by changing a dependency, which would blank
  // the panels while the new figures load. Filled in once the polls exist.
  const refreshAccount = useRef<() => void>(() => {});
  const account = useAccountEvents(!onPerps && !paused, () => refreshAccount.current());
  const polledPortfolio = useLive(
    getPortfolio,
    account.live ? every(ACCOUNT_BACKUP_MS) : every(PORTFOLIO_MS),
    [],
    paused,
    150,
  );
  // The options polls stop while the perpetuals desk is open, and vice versa.
  // Two desks' worth of requests for one desk on screen is how a rate limit gets
  // spent on panels nobody is looking at.

  // Positions, contract limits and stream health, on a slow poll. Prices do not
  // come from here any more - they are pushed, below - so this no longer needs to
  // run every couple of seconds.
  const perps = useLive(getPerpsDesk, PERPS_MS, [basketNonce], paused || !onPerps, 100);
  // Prices, pushed. The socket to the exchange was always there; this is the half
  // that was missing, and why /api/perps was being called every two seconds.
  const streamed = useTickStream("/api/perps/stream", onPerps && !paused);
  // no request at all while the chain is hidden
  const polledChain = useLive(
    () => getOptionChain(symbol, depth, expiry),
    every(CHAIN_MS),
    [symbol, expiry, depth, chainOpen],
    paused || !chainOpen,
    1050,
  );
  const polledBaskets = useLive(
    () => getBaskets(true),
    every(BASKETS_MS),
    [basketNonce],
    paused || onPerps,
    300,
  );
  const context = useLive(
    () => getMarketContext(symbol),
    every(CONTEXT_MS),
    [symbol],
    paused || onPerps,
    850,
  );
  // Prices pushed by the options venue's socket: the indices always, and every
  // contract the desk is showing - position legs, basket legs, the chain's
  // strikes. Their figures below are the polled ones moved to the streamed
  // price; OI and greeks stay as polled.
  const watched = watchedSymbols(
    polledPortfolio.data,
    polledBaskets.data,
    chainOpen ? polledChain.data : null,
  ).join(",");
  const indexTicks = useTickStream(
    `/api/quotes/stream${watched ? `?symbols=${encodeURIComponent(watched)}` : ""}`,
    !onPerps && !paused,
  );
  const indexLive = indexTicks.connected && Object.keys(indexTicks.prices).length > 0;
  // While the stream delivers, the quotes are read once - for the previous
  // close the day's change is measured from - and not polled at all.
  const polledQuotes = useLive(
    () => getQuotes(WATCHLIST),
    indexLive ? 0 : every(QUOTES_MS),
    [],
    paused || onPerps,
    600,
  );
  // Polled quotes with the streamed price laid over them. Only the price: the
  // previous close stays the poll's, so every change on the desk is measured
  // from the same figure.
  const quotes = useMemo(() => {
    const data = polledQuotes.data;
    if (!data) return polledQuotes;
    const merged: typeof data = { ...data };
    for (const [sym, tick] of Object.entries(indexTicks.prices)) {
      const q = merged[sym];
      if (q) merged[sym] = { ...q, ltp: tick.price };
    }
    return { ...polledQuotes, data: merged };
  }, [polledQuotes, indexTicks.prices]);
  const portfolio = useMemo(
    () => ({ ...polledPortfolio, data: livePortfolio(polledPortfolio.data, indexTicks.prices) }),
    [polledPortfolio, indexTicks.prices],
  );
  const baskets = useMemo(
    () => ({ ...polledBaskets, data: liveBaskets(polledBaskets.data, indexTicks.prices) }),
    [polledBaskets, indexTicks.prices],
  );
  const chain = useMemo(
    () => ({ ...polledChain, data: liveChain(polledChain.data, indexTicks.prices) }),
    [polledChain, indexTicks.prices],
  );
  const news = useLive(
    () => getNews(40, shownTopics),
    NEWS_MS,
    [shownTopics.join(",")],
    paused,
    1900,
  );
  const events = useLive(() => getEvents(45, "HM"), EVENTS_MS, [], paused, 2400);
  // Implied volatility moves on the scale of a session rather than a tick, and
  // each pass is a chain fetch - so this is the slowest poll on the desk.
  const vol = useLive(
    () => getVolatility(symbol),
    every(VOL_MS),
    [symbol],
    paused || onPerps,
    2700,
  );
  const history = useLive(
    () => getPortfolioHistory(7),
    every(BASKETS_MS),
    [basketNonce],
    paused || onPerps,
    1500,
  );

  const refreshPortfolioNow = polledPortfolio.refresh;
  const refreshBaskets = polledBaskets.refresh;
  const refreshHistory = history.refresh;
  useEffect(() => {
    refreshAccount.current = () => {
      refreshPortfolioNow();
      refreshBaskets();
      refreshHistory();
    };
  }, [refreshPortfolioNow, refreshBaskets, refreshHistory]);

  // With the market shut the portfolio is read once, but an order can still be
  // placed after hours - so coming back to the page reads it again.
  const refreshPortfolio = portfolio.refresh;
  useEffect(() => {
    if (nseOpen) return;
    const onShow = () => {
      if (document.visibilityState === "visible") refreshPortfolio();
    };
    document.addEventListener("visibilitychange", onShow);
    return () => document.removeEventListener("visibilitychange", onShow);
  }, [nseOpen, refreshPortfolio]);

  const now = useNow(!paused);
  // The newest figure on screen, polled or pushed: with the streams up the polls
  // are slow by design, and a tick is as fresh as a figure gets.
  const lastAt = useMemo(
    () =>
      Math.max(
        portfolio.at ?? 0,
        quotes.at ?? 0,
        ...Object.values(indexTicks.prices).map((t) => t.at),
      ),
    [portfolio.at, quotes.at, indexTicks.prices],
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
  // Figures are only stale if they should be moving.
  const stale = nseOpen && agoSeconds !== null && agoSeconds > PORTFOLIO_MS / 1000 + 8;

  // quotes cover every symbol and always poll; the chain is only open sometimes
  const spot =
    indexTicks.prices[symbol]?.price ??
    quotes.data?.[symbol]?.ltp ??
    context.data?.spot ??
    chain.data?.underlying_ltp ??
    null;
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

  // The pushed price wins where there is one, and the polled figure stands in
  // until the first tick arrives - so a freshly opened desk is not blank.
  const livePrices = useMemo(
    () =>
      (perps.data?.prices ?? []).map((p) => {
        const live = streamed.prices[p.symbol];
        if (live === undefined) return p;
        return {
          ...p,
          price: live.price,
          change_pct: live.change_pct ?? p.change_pct,
          // From the ticking clock rather than Date.now(): reading the wall clock
          // during render makes the value change without a reason to re-render,
          // and `useNow` is already here for exactly this.
          age_seconds: (now - live.at) / 1000,
        };
      }),
    [perps.data, streamed.prices, now],
  );

  /* Keyed by symbol, for the panels that take a price per instrument rather
     than a list. An instrument the venue has not priced yet is left out rather
     than carried as a null: those panels already draw a missing price, and a
     null in a map of numbers is only a way of saying the same thing that every
     reader has to remember to check. */
  const priceBySymbol = useMemo(
    () =>
      Object.fromEntries(
        livePrices.flatMap((p) => (p.price === null ? [] : [[p.symbol, p.price] as const])),
      ),
    [livePrices],
  );

  const onBasketChanged = useCallback(() => {
    setBasketNonce((n) => n + 1);
    portfolio.refresh();
  }, [portfolio]);

  return (
    <div className={onPerps ? "ws perps" : "ws"}>
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
        closedUntil={!onPerps && !nseOpen ? (health.data?.nse?.next_open ?? null) : null}
        paused={paused}
        onPause={() => setPaused((p) => !p)}
        theme={theme}
        onTheme={onTheme}
        context={context.data}
        venues={venues.data ?? []}
        venueId={venueId}
        onVenue={onVenue}
        onHome={onHome}
        perps={onPerps ? (perps.data ?? null) : null}
        pushing={streamed.connected}
        funds={
          onPerps ? (
            <PerpsFunds
              positions={perps.data?.positions ?? []}
              prices={priceBySymbol}
              quoteCurrency={perps.data?.quote_currency ?? "USDT"}
              moneyCurrency={perps.data?.money_currency ?? "INR"}
            />
          ) : null
        }
        alerts={
          <div className="bellwrap">
            <AlertsBell
              alerts={alerts.data?.alerts ?? []}
              activeCount={alerts.data?.active.length ?? 0}
              watching={alerts.data?.watcher.running ?? false}
              open={alertsOpen}
              onToggle={() => setAlertsOpen((o) => !o)}
            />
            {alertsOpen && (
              <>
                {/* Clicking anywhere else shuts it. A panel that only closes by
                    the button it opened from is a panel people leave open. */}
                <div className="bellaway" onClick={() => setAlertsOpen(false)} />
                <AlertsPanel
                  alerts={alerts.data?.alerts ?? []}
                  activeCount={alerts.data?.active.length ?? 0}
                  watches={alerts.data?.watches ?? []}
                  symbols={
                    onPerps
                      ? (perps.data?.instruments ?? []).map((i) => ({
                          id: i.symbol,
                          name: i.name,
                        }))
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
              </>
            )}
          </div>
        }
      />

      <div id="left">
        {onPerps ? (
          <PerpsWatch
            instruments={perps.data?.instruments ?? []}
            prices={livePrices}
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
          <PerpsTicket
            instruments={perps.data?.instruments ?? []}
            prices={priceBySymbol}
            symbol={perpSymbol}
            quoteCurrency={perps.data?.quote_currency ?? "USDT"}
            onPlaced={perps.refresh}
          />
        )}
        {onPerps && (
          <PerpsPositions
            positions={perps.data?.positions ?? []}
            prices={priceBySymbol}
            error={perps.data?.positions_error ?? null}
            quoteCurrency={perps.data?.quote_currency ?? "USDT"}
            moneyCurrency={perps.data?.money_currency ?? "INR"}
            onChanged={perps.refresh}
          />
        )}
        {!onPerps && (
          <VolPanel vol={vol.data} error={blockingOnly(vol.error)} loading={vol.loading} />
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

      {!onPerps && (
        <IndexChart
          symbol={symbol}
          name={UNDERLYINGS.find((u) => u.id === symbol)?.name ?? symbol}
          last={spot}
          walls={context.data ?? null}
          live={nseOpen}
        />
      )}

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

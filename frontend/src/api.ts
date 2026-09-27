export const API_BASE = "http://127.0.0.1:8000";

export interface Position {
  symbol: string;
  net_quantity: number;
  average_price: number;
  ltp: number;
  unrealized_pnl: number;
  product_type: string;
}

export interface PortfolioResponse {
  positions: Position[];
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
}

export interface Leg {
  option_type: "CE" | "PE";
  strike: number;
  premium: number;
  quantity: number;
  side: "BUY" | "SELL";
  symbol: string | null;
}

export interface RiskCheck {
  passed: boolean;
  reason: string;
}

export interface PayoffPoint {
  spot: number;
  payoff: number;
}

export interface StrategySignalResponse {
  strategy: string;
  symbol: string;
  underlying_ltp: number;
  legs: Leg[];
  // null means unbounded (the backend can't send Infinity - it isn't valid JSON).
  max_profit: number | null;
  max_loss: number | null;
  breakevens: number[];
  payoff_curve: PayoffPoint[];
  /** Mark-to-market now. Empty when the feed had no usable IV to price with. */
  payoff_curve_today: PayoffPoint[];
  days_to_expiry: number | null;
  pre_trade_checks: RiskCheck[];
  can_place: boolean;
}

export interface OrderResult {
  order_id: string;
  message: string;
}

export interface PlaceOrderResponse {
  orders: OrderResult[];
  basket_id: number;
}

export interface BasketLeg {
  id: number;
  symbol: string;
  option_type: "CE" | "PE";
  strike: number;
  side: "BUY" | "SELL";
  quantity: number;
  entry_price: number;
  entry_at: string;
  exit_price: number | null;
  exit_at: string | null;
  is_open: boolean;
  /** Live state, present only when fetched with live=true. Greeks are
      per contract: delta per point, theta per day, vega per volatility point. */
  ltp: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  iv: number | null;
  ltp_change: number | null;
  oi_change: number | null;
}

export interface Basket {
  id: number;
  name: string;
  strategy: string;
  underlying_symbol: string;
  created_at: string;
  /** This structure's own alert levels. Null means no level set. */
  stop_loss: number | null;
  profit_target: number | null;
  delta_limit: number | null;
  /** Overrides for the shared defaults. Null means the default is used. */
  worst_case_limit: number | null;
  short_delta_limit: number | null;
  expiry_warn_days: number | null;
  /** What the open legs are worth now, and how the structure leans — computed
      server-side, so the screen and the alert about it read one number. Null
      when the broker has not priced every open leg. */
  mtm: number | null;
  /** Exposure: deltas weighted by contracts. The only one that converts to money. */
  net_delta: number | null;
  /** The directional sum of the quoted deltas, unweighted — the figure on the
      legs table and the scale a delta limit is set in. */
  net_delta_per_contract: number | null;
  legs: BasketLeg[];
  max_profit: number | null;
  max_loss: number | null;
  breakevens: number[];
  payoff_curve: PayoffPoint[];
  /** Only present when fetched with live=true and the expiry could be resolved. */
  payoff_curve_today: PayoffPoint[];
  days_to_expiry: number | null;
  expiry_date: string | null;
  /** False for a calendar or diagonal: the payoff fields are then empty,
      because they are computed at a single expiry and would be wrong. */
  single_expiry: boolean;
}

export interface PortfolioHistoryPoint {
  fetched_at: string;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
}

/**
 * A failure the desk can explain rather than just show as red text.
 *
 * The backend classifies broker trouble into a stable `code` (see
 * broker/errors.py), so "rate limited" can be presented as a passing
 * condition while "sign-in expired" tells you to go and do something.
 */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }

  /** Transient: the figures on screen are simply a little behind. */
  get isTransient(): boolean {
    return this.code === "rate_limited";
  }
}

async function toApiError(path: string, response: Response): Promise<ApiError> {
  const text = await response.text();
  try {
    const body = JSON.parse(text);
    const detail = body?.detail;
    if (detail && typeof detail === "object" && typeof detail.code === "string") {
      return new ApiError(detail.code, detail.message ?? detail.code, response.status);
    }
    if (typeof detail === "string") {
      return new ApiError("request_failed", detail, response.status);
    }
  } catch {
    // not JSON - fall through
  }
  return new ApiError("request_failed", `${path} failed: ${response.status}`, response.status);
}

async function getJson<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`);
  } catch {
    // the backend itself is not answering, which is different from the broker
    throw new ApiError("api_unreachable", "Cannot reach the backend. Is uvicorn running?", 0);
  }
  if (!response.ok) {
    throw await toApiError(path, response);
  }
  return (await response.json()) as T;
}

async function extractErrorMessage(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const body = JSON.parse(text);
    if (body?.detail?.reasons) {
      return (body.detail.reasons as string[]).join("; ");
    }
    if (typeof body?.detail === "string") {
      return body.detail;
    }
  } catch {
    // not JSON - fall through to raw text below
  }
  return `${response.status} ${text}`;
}

async function postJson<TRequest, TResponse>(path: string, body: TRequest): Promise<TResponse> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(await extractErrorMessage(response));
  }
  return (await response.json()) as TResponse;
}

async function putJson<TRequest, TResponse>(path: string, body: TRequest): Promise<TResponse> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(await extractErrorMessage(response));
  }
  return (await response.json()) as TResponse;
}

async function del(path: string): Promise<void> {
  const response = await fetch(`${API_BASE}${path}`, { method: "DELETE" });
  if (!response.ok) {
    throw new Error(await extractErrorMessage(response));
  }
}

/** Forget a grouping. Sends nothing to the broker — the position stays open. */
export function deleteBasket(id: number): Promise<void> {
  return del(`/api/baskets/${id}`);
}

/** Take a leg out of a basket it never belonged to. Not an exit — see closeBasketLeg. */
export function removeBasketLeg(basketId: number, legId: number): Promise<void> {
  return del(`/api/baskets/${basketId}/legs/${legId}`);
}

export function getPortfolio(): Promise<PortfolioResponse> {
  return getJson<PortfolioResponse>("/api/portfolio");
}

export function getStrategySignal(
  strategy: string,
  symbol: string,
  quantity: number,
  expiry = "",
): Promise<StrategySignalResponse> {
  const params = new URLSearchParams({ symbol, quantity: String(quantity) });
  if (expiry) params.set("expiry", expiry);
  return getJson<StrategySignalResponse>(`/api/strategies/${strategy}?${params}`);
}

export function getPortfolioHistory(days = 7): Promise<PortfolioHistoryPoint[]> {
  return getJson<PortfolioHistoryPoint[]>(`/api/portfolio/history?days=${days}`);
}

export function placeOrder(
  strategy: string,
  symbol: string,
  quantity: number,
  expiry = "",
): Promise<PlaceOrderResponse> {
  return postJson<
    { strategy: string; symbol: string; quantity: number; expiry: string },
    PlaceOrderResponse
  >("/api/orders/place", { strategy, symbol, quantity, expiry });
}

export function getBaskets(live = false): Promise<Basket[]> {
  return getJson<Basket[]>(`/api/baskets${live ? "?live=true" : ""}`);
}

export function getBasket(id: number): Promise<Basket> {
  return getJson<Basket>(`/api/baskets/${id}`);
}

export interface NewBasketLegInput {
  symbol: string;
  option_type: "CE" | "PE";
  strike: number;
  side: "BUY" | "SELL";
  quantity: number;
  entry_price: number;
}

export function createBasket(
  name: string,
  strategy: string,
  underlyingSymbol: string,
  legs: NewBasketLegInput[],
): Promise<Basket> {
  return postJson<
    { name: string; strategy: string; underlying_symbol: string; legs: NewBasketLegInput[] },
    Basket
  >("/api/baskets", { name, strategy, underlying_symbol: underlyingSymbol, legs });
}

/** Alert levels for one structure. Null clears a level. */
export interface BasketLevels {
  stop_loss: number | null;
  profit_target: number | null;
  delta_limit: number | null;
  worst_case_limit: number | null;
  short_delta_limit: number | null;
  expiry_warn_days: number | null;
}

export function setBasketLevels(id: number, levels: BasketLevels): Promise<Basket> {
  return putJson<BasketLevels, Basket>(`/api/baskets/${id}/levels`, levels);
}

export function closeBasketLeg(basketId: number, legId: number, exitPrice: number): Promise<Basket> {
  return postJson<{ exit_price: number }, Basket>(
    `/api/baskets/${basketId}/legs/${legId}/close`,
    { exit_price: exitPrice },
  );
}

export interface Greeks {
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  iv: number;
}

export interface OptionChainRow {
  symbol: string;
  strike: number;
  option_type: "CE" | "PE";
  ltp: number;
  bid: number;
  ask: number;
  oi: number;
  prev_oi: number;
  volume: number;
  ltp_change: number;
  ltp_change_pct: number;
  oi_change: number;
  oi_change_pct: number;
  greeks: Greeks | null;
}

export interface Expiry {
  date: string; // "29-10-2026"
  token: string; // the broker's own selector, passed back verbatim
  weekly: boolean;
}

export interface OptionChain {
  underlying_symbol: string;
  underlying_ltp: number;
  fetched_at: string;
  rows: OptionChainRow[];
  expiries: Expiry[];
  expiry_token: string | null;
  call_oi: number;
  put_oi: number;
  india_vix: number | null;
}

export interface Quote {
  symbol: string;
  ltp: number;
  open: number;
  high: number;
  low: number;
  prev_close: number;
  volume: number;
  bid: number;
  ask: number;
  timestamp: string;
}

export function getOptionChain(
  symbol: string,
  strikeCount = 15,
  expiry = "",
): Promise<OptionChain> {
  const params = new URLSearchParams({ strike_count: String(strikeCount) });
  if (expiry) params.set("expiry", expiry);
  return getJson<OptionChain>(`/api/option-chain/${encodeURIComponent(symbol)}?${params}`);
}

/** One call for the whole watchlist, instead of a chain per underlying. */
export function getQuotes(symbols: readonly string[]): Promise<Record<string, Quote>> {
  return getJson<Record<string, Quote>>(
    `/api/quotes?symbols=${encodeURIComponent(symbols.join(","))}`,
  );
}

export interface Health {
  status: string;
  /** True while reads are being served from cache over a broker rate limit. */
  rate_limited: boolean;
}

export interface MarketContext {
  underlying_symbol: string;
  spot: number;
  change: number;
  change_pct: number;
  expiry_date: string | null;
  futures_symbol: string | null;
  futures: number | null;
  futures_premium: number | null;
  carry_pct: number | null;
  atm_strike: number | null;
  atm_straddle: number | null;
  atm_iv: number | null;
  historical_vol: number | null;
  iv_over_hv: number | null;
  put_call_ratio: number | null;
  max_pain: number | null;
  resistance: number | null;
  resistance_prominence: number | null;
  resistance_heaviest: number | null;
  support: number | null;
  support_prominence: number | null;
  support_heaviest: number | null;
  skew: number | null;
}

export function getMarketContext(symbol: string): Promise<MarketContext> {
  return getJson<MarketContext>(`/api/market/${encodeURIComponent(symbol)}`);
}

export interface CalendarEvent {
  day: string;
  name: string;
  label: string;
  importance: "H" | "M" | "L";
  coverage: "india" | "global";
  country: string | null;
}

export interface EventsResponse {
  events: CalendarEvent[];
  /** Seconds since the calendar was refreshed; null means never. */
  age_seconds: number | null;
  /** Set when the refresh failed. The events may still be usable, just stale. */
  error: string | null;
}

export interface Headline {
  title: string;
  link: string;
  source: string;
  published: string | null;
  /** Its publisher's beat — taken from the source, not read out of the title. */
  topics: string[];
}

export interface NewsResponse {
  headlines: Headline[];
  age_seconds: number | null;
  error: string | null;
  /** Every topic the desk has a source for, so the filter offers what exists. */
  available_topics: string[];
  /** Topics per publisher, for naming what a filter would include. */
  sources: Record<string, string[]>;
}

export function getEvents(days = 45, importance = "HM"): Promise<EventsResponse> {
  return getJson<EventsResponse>(`/api/events?days=${days}&importance=${importance}`);
}

export function getNews(limit = 40, topics: readonly string[] = []): Promise<NewsResponse> {
  const filter = topics.length ? `&topics=${encodeURIComponent(topics.join(","))}` : "";
  return getJson<NewsResponse>(`/api/news?limit=${limit}${filter}`);
}

export function getHealth(): Promise<Health> {
  return getJson<Health>("/api/health");
}

/* -------------------------------------------------------------------------
 * Venues and the perpetuals desk
 *
 * Two desks, not one desk with a filter: index options and perpetual futures
 * share almost no vocabulary. A chain means nothing on one, leverage and a
 * liquidation price mean nothing on the other, and half the fields would be null
 * either way.
 * ---------------------------------------------------------------------- */

export interface Venue {
  id: string;
  name: string;
  asset_class: "index_options" | "perpetuals";
  quote_currency: string;
  session: string;
  capabilities: string[];
}

export interface PerpInstrument {
  symbol: string;
  name: string;
  quote_asset: string;
  /** The venue's own ceiling for this contract — 150× on BTC, 75× gold, 50× oil. */
  max_leverage: number;
  /** Smallest order the venue accepts at the current price. Usually set by a
      notional floor, so it moves with the price. */
  min_quantity: number;
  min_notional: number;
  /** Decimal places the venue prices in, so a tile does not invent precision. */
  price_dp: number;
  quantity_dp: number;
  open: boolean;
}

export interface PerpPrice {
  symbol: string;
  /** Null until the stream has carried it. Never zero, which would be a market
      at nothing. */
  price: number | null;
  /** Seconds since it arrived, so a dead stream reads as stale rather than current. */
  age_seconds: number | null;
  /** The venue's own 24-hour change, as a percentage. A market with no close has
      no yesterday of ours to measure against. */
  change_pct: number | null;
}

export interface StreamStatus {
  connected: boolean;
  ticks: number;
  dropped: number;
  subscribers: number;
}

export interface PerpPosition {
  symbol: string;
  name: string;
  side: string;
  quantity: number;
  entry_price: number;
  price: number | null;
  leverage: number;
  margin_type: string;
  /** In the desk's money currency, not the currency the price is in. */
  margin: number;
  /** The same in the account's money — what it is actually debited. */
  margin_in_margin_asset: number | null;
  /** Margin currency per unit of quote currency, for showing a live figure in
      the money the account is kept in. */
  conversion_rate: number | null;
  unrealized_pnl: number | null;
  /** True when we worked the P&L out from the price because the venue gave none.
      A figure we derived should not be shown as the venue's. */
  pnl_is_ours: boolean;
  liquidation_price: number | null;
  /** Fraction of price. Comparable across instruments; a points difference is not. */
  liquidation_distance: number | null;
  position_id: string;
  /** Whether the exchange is holding a stop for this position. An exchange-held
      stop fires with this app closed; its absence means nothing closes the
      position but the market. */
  protected: boolean;
  take_profit_orders: number;
  stop_loss_orders: number;
}

export interface PerpOrder {
  symbol: string;
  side: "BUY" | "SELL";
  order_type: "MARKET" | "LIMIT";
  quantity: number;
  leverage: number;
  /** ISOLATED risks only the margin behind the position; CROSS puts the rest of
      the account behind it. */
  margin_mode: "ISOLATED" | "CROSS";
  limit_price?: number | null;
}

export interface OrderCheck {
  passed: boolean;
  reason: string;
}

export interface PerpOrderResult {
  /** Whether it actually left. False means refused, and `reasons` says why. */
  sent: boolean;
  checks: OrderCheck[];
  reasons: string[];
  /** What happened, in the venue's words when the venue decided. */
  outcome: string;
  notional: number;
  price: number | null;
  venue_order_id: string | null;
  record_id: number;
}

export function placePerpOrder(order: PerpOrder): Promise<PerpOrderResult> {
  return postJson<PerpOrder, PerpOrderResult>("/api/perps/orders", order);
}

export interface PerpOrderRecord {
  id: number;
  at: string;
  symbol: string;
  side: string;
  order_type: string;
  quantity: number;
  price: number | null;
  leverage: number;
  notional: number;
  sent: boolean;
  reason: string;
  venue_order_id: string | null;
}

export function getPerpOrders(limit = 50): Promise<PerpOrderRecord[]> {
  return getJson<PerpOrderRecord[]>(`/api/perps/orders?limit=${limit}`);
}

export interface CloseResult {
  closed: boolean;
  outcome: string;
  venue_order_id: string | null;
  record_id: number;
}

/** Close a position at the market, for its full size. Reduce-only at the venue. */
export function closePerpPosition(positionId: string): Promise<CloseResult> {
  return postJson<Record<string, never>, CloseResult>(
    `/api/perps/positions/${encodeURIComponent(positionId)}/close`,
    {},
  );
}

export interface Protection {
  quantity: number;
  take_profit?: number | null;
  stop_loss?: number | null;
}

/** Ask the venue to hold a take-profit and stop-loss against a position. */
export async function setProtection(positionId: string, body: Protection): Promise<void> {
  const response = await fetch(
    `${API_BASE}/api/perps/positions/${encodeURIComponent(positionId)}/protection`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!response.ok) throw new Error(await extractErrorMessage(response));
}

export interface PerpsDesk {
  venue: string;
  name: string;
  /** Prices and charts are in this. */
  quote_currency: string;
  /** Balances and P&L are in this, which is not the same on this venue. */
  money_currency: string;
  instruments: PerpInstrument[];
  prices: PerpPrice[];
  positions: PerpPosition[];
  /** Why the list is empty, when it is empty because something failed. "No
      positions" is a dangerous thing to show wrongly on a leveraged book. */
  positions_error: string | null;
  stream: StreamStatus;
}

export interface Candle {
  at: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export function getVenues(): Promise<Venue[]> {
  return getJson<Venue[]>("/api/venues");
}

export function getPerpsDesk(): Promise<PerpsDesk> {
  return getJson<PerpsDesk>("/api/perps");
}

/** One indicator from the strategy, aligned bar for bar with the candles. */
export interface IndicatorLine {
  label: string;
  name: string;
  length: number;
  interval: string | null;
  /** Whether it is a price and belongs on the price axis. An RSI runs 0–100 and
      a pivot-gap percentile likewise; drawn against price they would flatten
      every candle into a line at the bottom of the chart. */
  on_price: boolean;
  /** Null where the indicator was not yet defined, or — on a higher timeframe —
      repeated across the bars for which that value was the newest closed one. */
  values: (number | null)[];
}

export interface CandlesResponse {
  /** Whose candles these are. On a chart with an order ticket beside it this is not
      decoration: a price from a source you are not trading is the wrong price. */
  source: string;
  /** Why the series may be short or stale, when there is a reason worth saying. */
  note: string;
  candles: Candle[];
  /** Indicators that were asked for, computed by the backend through the same
      code a backtest reads them with — so the EMA drawn here and the EMA a rule
      would trade on are the same number. */
  lines: IndicatorLine[];
}

export function getPerpCandles(
  symbol: string,
  resolution: string,
  days: number,
  /** "ema:20,ema:50,rsi:14" — or with a timeframe, "ema:50:4h". */
  indicators = "",
): Promise<CandlesResponse> {
  const query = new URLSearchParams({ resolution, days: String(days) });
  if (indicators) query.set("indicators", indicators);
  return getJson<CandlesResponse>(
    `/api/perps/candles/${encodeURIComponent(symbol)}?${query}`,
  );
}

/* -------------------------------------------------------------------------
 * Alerts
 *
 * Raised by the backend, not here. The engine used to run in this tab, which
 * meant nothing was watching once it was closed and Telegram could never work
 * at all. What is left on this side is display: the log, the thresholds, and
 * the levels you asked about.
 * ---------------------------------------------------------------------- */

export type Severity = "risk" | "warn" | "target" | "info";

export interface Alert {
  /** Stable per condition, so one condition is one alert however often it is polled. */
  key: string;
  severity: Severity;
  /** Which structure it concerns, or null for account-wide ones. */
  subject: string | null;
  message: string;
  /** Epoch milliseconds. */
  at: number;
  /** When it was delivered off-screen, or null if it has not been. */
  notified_at: number | null;
}

export interface Limits {
  target: number;
  daily_loss: number;
  max_loss: number;
  short_delta: number;
  expiry_days: number;
}

export type WatchKind = "price" | "pnl";
export type WatchDirection = "above" | "below";

/** A level you asked to be told about. */
export interface Watch {
  id: number;
  kind: WatchKind;
  symbol: string | null;
  direction: WatchDirection;
  level: number;
  note: string;
  enabled: boolean;
}

export interface NewWatch {
  kind: WatchKind;
  direction: WatchDirection;
  level: number;
  symbol?: string | null;
  note?: string;
}

/** Whether anything is actually watching. An empty log only means calm if so. */
export interface WatcherStatus {
  running: boolean;
  last_run_at: number | null;
  last_error: string | null;
  /** Whether alerts are being delivered anywhere off-screen. */
  telegram: boolean;
}

export interface AlertsResponse {
  alerts: Alert[];
  /** Conditions true right now, whether or not they fired this pass. */
  active: string[];
  limits: Limits;
  watches: Watch[];
  watcher: WatcherStatus;
}

export const SEVERITY_LABEL: Record<Severity, string> = {
  risk: "RISK",
  warn: "WARN",
  target: "TARGET",
  info: "INFO",
};

/**
 * Whether a calendar entry bears on an Indian index position.
 *
 * Kept on this side purely for the top bar's "next event", which picks one from
 * the calendar it already has rather than asking for it. The backend applies the
 * same rule when it decides what to alert on - `alerting/rules.py` - and that
 * copy is the one that matters.
 */
export function affectsIndia(event: CalendarEvent): boolean {
  if (event.importance !== "H") return false;
  return event.coverage === "india" || event.country === "United States";
}

export function getAlerts(limit = 200): Promise<AlertsResponse> {
  return getJson<AlertsResponse>(`/api/alerts?limit=${limit}`);
}

export async function clearAlerts(): Promise<void> {
  const response = await fetch(`${API_BASE}/api/alerts/clear`, { method: "POST" });
  if (!response.ok) throw new Error(await extractErrorMessage(response));
}

export function saveLimits(limits: Limits): Promise<Limits> {
  return putJson<Limits, Limits>("/api/alerts/limits", limits);
}

export function addWatch(watch: NewWatch): Promise<Watch> {
  return postJson<NewWatch, Watch>("/api/alerts/watches", watch);
}

export async function setWatchEnabled(id: number, enabled: boolean): Promise<Watch[]> {
  const response = await fetch(
    `${API_BASE}/api/alerts/watches/${id}/enabled?enabled=${enabled}`,
    { method: "POST" },
  );
  if (!response.ok) throw new Error(await extractErrorMessage(response));
  return (await response.json()) as Watch[];
}

export function deleteWatch(id: number): Promise<void> {
  return del(`/api/alerts/watches/${id}`);
}

/** The strategies the backend actually registers (api/app.py `_strategies`). */
export const STRATEGIES = [
  { id: "iron_condor", label: "Iron condor" },
  { id: "short_strangle", label: "Short strangle" },
  { id: "credit_spread_bullish", label: "Bull put spread" },
  { id: "credit_spread_bearish", label: "Bear call spread" },
] as const;

/** India VIX rides along in the watchlist: it is read, never traded. */
export const INDIA_VIX = { id: "NSE:INDIAVIX-INDEX", name: "INDIA VIX", ex: "NSE" } as const;

/** Index underlyings, in the symbol format the broker layer expects. */
export const UNDERLYINGS = [
  { id: "NSE:NIFTY50-INDEX", name: "NIFTY 50", ex: "NSE" },
  { id: "NSE:NIFTYBANK-INDEX", name: "BANK NIFTY", ex: "NSE" },
  { id: "NSE:FINNIFTY-INDEX", name: "FIN NIFTY", ex: "NSE" },
  { id: "NSE:MIDCPNIFTY-INDEX", name: "MIDCAP NIFTY", ex: "NSE" },
  { id: "BSE:SENSEX-INDEX", name: "SENSEX", ex: "BSE" },
] as const;

/** Everything the watchlist quotes: the tradable underlyings plus India VIX. */
export const WATCHLIST: readonly string[] = [...UNDERLYINGS.map((u) => u.id), INDIA_VIX.id];

/** A failure as the desk should present it: what to say, and how loudly. */
export function describeError(error: Error | null): { text: string; transient: boolean } | null {
  if (!error) return null;
  if (error instanceof ApiError) {
    return { text: error.message, transient: error.isTransient };
  }
  return { text: error.message, transient: false };
}

export interface BarSeries {
  source: string;
  symbol: string;
  interval: string;
  bars: number;
  first: string | null;
  last: string | null;
}

/** Every bar series the store holds. What a backtest can honestly be run over. */
export function getBarSeries(): Promise<BarSeries[]> {
  return getJson<BarSeries[]>("/api/bars/series");
}

// --- Backtesting -----------------------------------------------------------

/** What an indicator operand can name. */
export type IndicatorName = "ema" | "sma" | "rsi" | "atr" | "pivot_gap" | "pivot_gap_rank";

/** One side of a comparison: an indicator, a price, a pivot level, or a number. */
export type Operand =
  | {
      kind: "indicator";
      name: IndicatorName;
      length: number;
      ago?: number;
      tf?: string;
    }
  | { kind: "price"; field: "open" | "high" | "low" | "close"; ago?: number; tf?: string }
  | { kind: "pivot"; level: string; ago?: number; tf?: string }
  | { kind: "value"; value: number };

export type Comparison = "crosses_above" | "crosses_below" | "above" | "below" | "equals";

export interface Compare {
  left: Operand;
  op: Comparison;
  right: Operand;
}

/** A group of comparisons, joined one way or the other. */
export interface Group {
  all?: Compare[];
  any?: Compare[];
}

export type LevelKind = "percent" | "candle" | "atr" | "pivot" | "reward";

export interface Level {
  kind: LevelKind;
  value?: number;
  length?: number;
  field?: string;
  level?: string;
  ago?: number;
  tf?: string;
}

/** A named session, or a window of your own with a real timezone. */
export type SessionChoice = string | { name?: string; start: string; end: string; tz: string };

/** What has to happen after the conditions before a trade is actually taken. */
export interface Trigger {
  kind: "break";
  /** Which price of the setup candle the order rests at. Mirrors for a short. */
  field: "high" | "low" | "close" | "open";
  /** Which candle, counting back from the one the conditions fired on. */
  ago?: number;
  /** How many candles the order rests for. */
  within: number;
  /** A cushion past the level, in basis points, always against you. */
  buffer_bps?: number;
}

export interface StrategySpec {
  name: string;
  /** Absent means enter at the next candle's open. */
  trigger?: Trigger | null;
  /** Hours during which entries may fire. Empty means all of them. */
  sessions?: SessionChoice[];
  /** Whether an open position is closed when the session ends. */
  close_outside_session?: boolean;
  long_entry?: Group | null;
  short_entry?: Group | null;
  long_exit?: Group | null;
  short_exit?: Group | null;
  stop?: Level | null;
  target?: Level | null;
}

export type Sizing = "equity" | "quantity" | "notional";

export interface BacktestRequest {
  spec: StrategySpec;
  source: string;
  symbol: string;
  interval: string;
  days: number;
  capital: number;
  leverage: number;
  slippage_bps: number;
  maker_entry: boolean;
  /** How each position is sized. */
  sizing: Sizing;
  /** For "equity": the fraction of the account committed as margin. */
  risk: number;
  /** For "quantity": the lot, in the instrument's own units. */
  quantity: number;
  /** For "notional": the position's value in the quote currency. */
  notional: number;
  min_quantity: number;
  min_notional: number;
  quantity_dp: number;
}

export interface BacktestMetrics {
  trades: number;
  wins: number;
  win_rate: number;
  total_return: number;
  buy_and_hold: number;
  beat_holding: boolean;
  max_drawdown: number;
  /** Compounded yearly return, or null over a window too short to annualise. */
  annualised: number | null;
  sharpe: number;
  /** Yearly return over the worst drawdown. Null when nothing ever fell. */
  calmar: number | null;
  gross: number;
  fees: number;
  funding: number;
  slippage: number;
  cost_share: number | null;
  exposure: number;
  best: number;
  worst: number;
  endings: Record<string, number>;
  by_side: Record<string, SideSummary>;
  average_bars_held: number;
}

/** One side of the book on its own — a strategy written both ways is two strategies. */
export interface SideSummary {
  trades: number;
  wins: number;
  win_rate: number;
  gross: number;
  net: number;
}

export interface BacktestTrade {
  side: string;
  opened_at: string;
  closed_at: string;
  entry: number;
  exit_price: number;
  quantity: number;
  /** Position value at entry, in the quote currency. */
  notional: number;
  why: string;
  /** The condition that opened it, in the words it was built with. */
  entry_reason: string;
  /** What closed it — the exit condition, or the stop, target or liquidation. */
  exit_reason: string;
  gross: number;
  fees: number;
  funding: number;
  slippage: number;
  net: number;
  /** Net against the margin the position tied up, not against the whole account. */
  net_pct: number;
  bars_held: number;
}

export interface BacktestResult {
  name: string;
  /** The strategy read back in words, so a result says what produced it. */
  reads: string;
  symbol: string;
  source: string;
  interval: string;
  bars: number;
  started: string | null;
  ended: string | null;
  capital: number;
  final: number;
  metrics: BacktestMetrics;
  /** [unix seconds, equity], thinned for drawing. */
  curve: [number, number][];
  trades: BacktestTrade[];
  trades_total: number;
  /** Positions closed and reopened the other way on the same signal. */
  reversals: number;
  /** Setups that armed a resting order, and those price never reached. */
  armed: number;
  expired_unfilled: number;
  skipped_too_small: number;
  skipped_unaffordable: number;
  caveats: string[];
}

export interface WindowResponse {
  source: string;
  symbol: string;
  interval: string;
  candles: Candle[];
  lines: IndicatorLine[];
}

/**
 * Bars over one window, for looking at a single trade.
 *
 * Read from the store only. A backtest runs over history already fetched, and
 * going to a source here could return bars that differ from the ones the numbers
 * were computed on.
 */
export function getBacktestCandles(request: {
  source: string;
  symbol: string;
  interval: string;
  start: string;
  end: string;
  spec?: StrategySpec;
}): Promise<WindowResponse> {
  return postJson<typeof request, WindowResponse>("/api/backtest/candles", request);
}

export function runBacktest(request: BacktestRequest): Promise<BacktestResult> {
  return postJson<BacktestRequest, BacktestResult>("/api/backtest/run", request);
}

// --- Relative rotation -----------------------------------------------------

export interface RrgIndexOption {
  id: string;
  name: string;
  /** How many things it would plot. Zero means its members have not been fetched. */
  plots: number;
  kind: "sectors" | "constituents";
}

export interface RrgOptions {
  indices: RrgIndexOption[];
  /** Listed apart from the things to plot: the Sensex publishes no constituent
      list, so it can only ever be a benchmark. */
  benchmarks: { id: string; name: string }[];
  timeframes: string[];
  benchmark: string;
  window: number;
  window_min: number;
  window_max: number;
}

export interface RrgPoint {
  at: string;
  ratio: number;
  momentum: number;
}

export type Quadrant = "leading" | "weakening" | "lagging" | "improving";

export interface RrgSeries {
  symbol: string;
  /** Short, because it is drawn on the graph. */
  label: string;
  name: string;
  /** Newest last. A path rather than a point, because the rotation is the
      information — and because it makes replay a slider rather than a request. */
  path: RrgPoint[];
  quadrant: Quadrant;
}

export interface RrgSnapshot {
  index_id: string;
  index_name: string;
  benchmark: string;
  benchmark_name: string;
  timeframe: string;
  window: number;
  series: RrgSeries[];
  /** Named rather than dropped: a missing dot is otherwise indistinguishable
      from one sitting under another. */
  missing: string[];
  members_as_at: string | null;
  caveats: string[];
}

export function getRrgOptions(): Promise<RrgOptions> {
  return getJson<RrgOptions>("/api/rrg/options");
}

export function getRrg(request: {
  index_id: string;
  timeframe: string;
  benchmark?: string;
  window?: number;
}): Promise<RrgSnapshot> {
  const query = new URLSearchParams({
    index_id: request.index_id,
    timeframe: request.timeframe,
  });
  if (request.benchmark) query.set("benchmark", request.benchmark);
  if (request.window) query.set("window", String(request.window));
  return getJson<RrgSnapshot>(`/api/rrg/snapshot?${query}`);
}

// --- Volatility ------------------------------------------------------------

export interface VolRank {
  value: number;
  low: number;
  high: number;
  /** Position within the high–low range. One spike flattens everything since. */
  rank: number;
  /** Share of days that were lower. Ignores how far away the extremes are. */
  percentile: number;
  /** How many readings are behind it — a rank over two months says so. */
  days: number;
  says: string;
}

export interface Realised {
  window: number;
  close_to_close: number | null;
  /** Only on the twenty-day window, where the comparison is worth drawing. */
  parkinson: number | null;
}

export interface Volatility {
  underlying: string;
  name: string;
  spot: number;
  expiry: string;
  days_to_expiry: number;
  atm_strike: number;
  atm_iv: number | null;
  straddle: number | null;
  /** What the options are priced to cover to this expiry. */
  expected_move_pct: number | null;
  expected_move_points: number | null;
  india_vix: number | null;
  realised: Realised[];
  /** Implied minus realised over twenty sessions — what a seller collects. */
  spread: number | null;
  /** Implied over realised. Travels between a quiet index and a wild one where
      the subtraction does not. */
  iv_hv: number | null;
  vix_rank: VolRank | null;
  /** Null until enough of our own implied history has been recorded. */
  iv_rank: VolRank | null;
  iv_days: number;
  caveats: string[];
}

export function getVolatility(underlying: string): Promise<Volatility> {
  return getJson<Volatility>(`/api/volatility/${encodeURIComponent(underlying)}`);
}

// --- Market structure ------------------------------------------------------

export interface Swing {
  at: string;
  kind: "high" | "low";
  price: number;
  /** False until k bars have printed after it — the next bar can revoke it. */
  confirmed: boolean;
}

export interface StructureBreak {
  at: string;
  price: number;
  level: number;
  /** True when it continues the prevailing structure; false is the first crack. */
  continuation: boolean;
}

export interface StructureFrame {
  interval: string;
  /** Bars the reading came from, which is also what the chart shows. */
  bars: number;
  /** The span those bars cover, in words — "180 bars" means nothing alone. */
  covers: string;
  trend: "uptrend" | "downtrend" | "broadening" | "contracting" | "unclear";
  says: string;
  high_label: string | null;
  low_label: string | null;
  swings: Swing[];
  last_break: StructureBreak | null;
  /** Only on the charted timeframe, to keep the payload small. */
  candles: { at: string; open: number; high: number; low: number; close: number }[];
  note: string;
}

export interface MarketStructure {
  underlying: string;
  name: string;
  k: number;
  /** Bars each reading looks back over, the same count at every size. */
  lookback: number;
  charted: string;
  frames: StructureFrame[];
  /** Where every size agrees, if they do. */
  agreement: string;
  caveats: string[];
}

export function getStructure(
  underlying: string,
  k: number,
  charted: string,
): Promise<MarketStructure> {
  const query = new URLSearchParams({ k: String(k), charted });
  return getJson<MarketStructure>(
    `/api/structure/${encodeURIComponent(underlying)}?${query}`,
  );
}

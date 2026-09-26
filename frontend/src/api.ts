const API_BASE = "http://127.0.0.1:8000";

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
  stop_loss: number | null;
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
}

export interface NewsResponse {
  headlines: Headline[];
  age_seconds: number | null;
  error: string | null;
}

export function getEvents(days = 45, importance = "HM"): Promise<EventsResponse> {
  return getJson<EventsResponse>(`/api/events?days=${days}&importance=${importance}`);
}

export function getNews(limit = 40): Promise<NewsResponse> {
  return getJson<NewsResponse>(`/api/news?limit=${limit}`);
}

export function getHealth(): Promise<Health> {
  return getJson<Health>("/api/health");
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

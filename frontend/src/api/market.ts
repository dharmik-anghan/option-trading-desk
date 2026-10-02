import { getJson } from "./http";

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

/** Strategy names a structure can be labelled with. */
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

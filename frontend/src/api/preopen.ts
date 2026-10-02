import { getJson } from "./http";

export interface PreOpenIndex {
  name: string;
  price: number;
  change: number;
  pct_change: number;
}

export interface PreOpenDay {
  day: string;
  /** `nse` from the API (with the book), `csv` from a downloaded file (without). */
  source: "nse" | "csv";
  as_of: string | null;
  rows: number;
  index: PreOpenIndex | null;
  advances: number;
  declines: number;
  unchanged: number;
}

export interface PreOpenDays {
  days: PreOpenDay[];
  recorder: { running: boolean; last_day: string | null; last_error: string | null };
}

export interface PreOpenLevel {
  price: number;
  buy_qty: number;
  sell_qty: number;
  is_iep: boolean;
}

export interface PreOpenQuote {
  symbol: string;
  name: string | null;
  industry: string | null;
  /** Today's membership of the option indices, not the membership on the day. */
  indices: string[];
  prev_close: number;
  final_price: number;
  final_quantity: number;
  change: number;
  pct_change: number;
  turnover_cr: number | null;
  ffm_cap_cr: number | null;
  best_bid: number | null;
  best_ask: number | null;
  total_buy_qty: number | null;
  total_sell_qty: number | null;
  year_high: number | null;
  year_low: number | null;
  book: PreOpenLevel[];
}

export interface PreOpenSession {
  day: PreOpenDay;
  quotes: PreOpenQuote[];
  filters: { id: string; name: string }[];
}

export function getPreOpenDays(): Promise<PreOpenDays> {
  return getJson<PreOpenDays>("/api/preopen/days");
}

export function getPreOpenSession(day: string): Promise<PreOpenSession> {
  return getJson<PreOpenSession>(`/api/preopen/days/${day}`);
}

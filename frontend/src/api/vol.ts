import { getJson } from "./http";

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

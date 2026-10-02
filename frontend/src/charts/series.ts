/**
 * One shape for a value over time, and the adapters into it.
 *
 * The backend answers in more than one spelling - unix seconds from the equity
 * backtest, ISO days from the options backtest, timestamped snapshots from the
 * portfolio - and a chart should not care which. Adapt at the edge; every line
 * chart takes `Point[]`.
 */
import type { PortfolioHistoryPoint } from "../api";

export interface Point {
  /** Milliseconds since the epoch. */
  at: number;
  value: number;
}

/** `[unix seconds, value]`, as the equity backtest sends its curve. */
export const fromUnixPairs = (pairs: readonly [number, number][]): Point[] =>
  pairs.map(([at, value]) => ({ at: at * 1000, value }));

/** `[ISO date or time, value]`, as the options backtest sends its equity. */
export const fromIsoPairs = (pairs: readonly [string, number][]): Point[] =>
  pairs.map(([at, value]) => ({ at: Date.parse(at), value }));

/** The portfolio's recorded snapshots, as total P&L. */
export const fromSnapshots = (snapshots: readonly PortfolioHistoryPoint[]): Point[] =>
  snapshots.map((s) => ({ at: Date.parse(s.fetched_at), value: s.total_pnl }));

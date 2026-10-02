import { getJson } from "./http";

export interface Candle {
  at: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
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

/**
 * Candles for any series, from the one endpoint both desks draw from.
 *
 * `source` names whose candles they are, and it is not a detail: a perpetual on
 * Shark, spot on Binance and an NSE index are three different instruments, and
 * a chart must never quietly substitute one for another.
 *
 * `bars` asks for the last N, which is how a panel asks for exactly the window
 * something beside it is describing. `days` is how far back to read, and is
 * worked out from `bars` when it is left out — the calendar arithmetic differs
 * at every size and the caller should not have to do it.
 */
export function getChart(
  source: string,
  symbol: string,
  opts: {
    interval: string;
    days?: number;
    bars?: number;
    /** "ema:20,ema:50,rsi:14" — or with a timeframe, "ema:50:4h". */
    indicators?: string;
  },
): Promise<CandlesResponse> {
  const query = new URLSearchParams({ interval: opts.interval });
  if (opts.days) query.set("days", String(opts.days));
  if (opts.bars) query.set("bars", String(opts.bars));
  if (opts.indicators) query.set("indicators", opts.indicators);
  return getJson<CandlesResponse>(
    `/api/chart/${encodeURIComponent(source)}/${encodeURIComponent(symbol)}?${query}`,
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

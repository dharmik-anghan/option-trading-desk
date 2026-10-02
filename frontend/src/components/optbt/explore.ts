import type { OptbtTrade } from "../../api";

/**
 * Slicing a finished run without running it again.
 *
 * Every trade carries the day it was taken on - weekday, days to expiry, VIX and
 * its percentile, the pivot zone the market opened in - so any of those can be
 * filtered after the fact and every figure recomputed from what is left.
 *
 * For intraday trades that is exact: one day's trade does not change the next,
 * so "Tuesdays out of a Monday-to-Friday run" is the same as a Tuesday-only run
 * (checked on the four-year straddle: 195 trades, +36,381 both ways). For a
 * positional strategy it is only approximate - holding one trade decides
 * whether the next is taken - and the strategy's own entry conditions are the
 * way to test a slice exactly.
 */
export interface Explore {
  weekdays: string[];
  expiry: "any" | "only" | "skip";
  /** A days-to-expiry bucket from DTE_ORDER, or "" for all. */
  dte: string;
  /** A VIX percentile bucket from VIX_ORDER, or "" for all. */
  vix: string;
  /** A key from OPENED, or "" for anywhere. */
  opened: string;
  ended: string;
  outcome: "all" | "won" | "lost";
  months: string[];
  years: string[];
}

export const NO_FILTER: Explore = {
  weekdays: [],
  expiry: "any",
  dte: "",
  vix: "",
  opened: "",
  ended: "",
  outcome: "all",
  months: [],
  years: [],
};

export const isFiltered = (f: Explore) => JSON.stringify(f) !== JSON.stringify(NO_FILTER);

/** Where the market opened, grouped the way a trader asks about it. */
export const OPENED: Record<string, { label: string; zones: string[] }> = {
  inside: { label: "Between S1 and R1", zones: ["S1-P", "P-R1"] },
  wide: { label: "Between S2 and R2", zones: ["S2-S1", "S1-P", "P-R1", "R1-R2"] },
  below: { label: "Below S1", zones: ["S2-S1", "below S2"] },
  above: { label: "Above R1", zones: ["R1-R2", "above R2"] },
};

export function apply(trades: OptbtTrade[], f: Explore): OptbtTrade[] {
  const zones = f.opened ? (OPENED[f.opened]?.zones ?? [f.opened]) : null;
  return trades.filter((t) => {
    const g = t.tags;
    if (!g) return true;
    if (f.weekdays.length && !f.weekdays.includes(g.weekday)) return false;
    if (f.expiry === "only" && !g.expiry_day) return false;
    if (f.expiry === "skip" && g.expiry_day) return false;
    if (f.dte && dteBucket(t) !== f.dte) return false;
    if (f.vix && vixBucket(t) !== f.vix) return false;
    if (zones && !zones.includes(g.open_zone ?? "")) return false;
    if (f.ended && t.ended !== f.ended) return false;
    if (f.outcome === "won" && !(t.net > 0)) return false;
    if (f.outcome === "lost" && t.net > 0) return false;
    if (f.months.length && !f.months.includes(g.month)) return false;
    if (f.years.length && !f.years.includes(g.month.slice(0, 4))) return false;
    return true;
  });
}

export interface Figures {
  trades: number;
  wins: number;
  gross: number;
  charges: number;
  net: number;
  average: number;
  median: number;
  best: number;
  worst: number;
  profitFactor: number | null;
  maxDrawdown: number;
  worstShare: number | null;
  costShare: number | null;
}

/** The same figures the server reports, from whichever trades are showing. */
export function figures(trades: OptbtTrade[]): Figures {
  const closed = trades.filter((t) => t.closed !== null);
  const nets = closed.map((t) => t.net);
  const gains = nets.filter((n) => n > 0).reduce((a, n) => a + n, 0);
  const losses = -nets.filter((n) => n < 0).reduce((a, n) => a + n, 0);
  const gross = closed.reduce((a, t) => a + t.gross, 0);
  const charges = closed.reduce((a, t) => a + t.charges, 0);
  const net = nets.reduce((a, n) => a + n, 0);
  const sorted = [...nets].sort((a, b) => a - b);
  const median = sorted.length
    ? sorted.length % 2
      ? sorted[(sorted.length - 1) / 2]
      : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2
    : 0;
  const worst5 = sorted.slice(0, 5).filter((n) => n < 0);
  let peak = 0;
  let drawdown = 0;
  for (const [, v] of curve(closed)) {
    peak = Math.max(peak, v);
    drawdown = Math.max(drawdown, peak - v);
  }
  return {
    trades: closed.length,
    wins: nets.filter((n) => n > 0).length,
    gross,
    charges,
    net,
    average: closed.length ? net / closed.length : 0,
    median,
    best: sorted.length ? sorted[sorted.length - 1] : 0,
    worst: sorted.length ? sorted[0] : 0,
    profitFactor: losses ? gains / losses : null,
    maxDrawdown: drawdown,
    worstShare: gains ? -worst5.reduce((a, n) => a + n, 0) / gains : null,
    costShare: gross > 0 ? charges / gross : null,
  };
}

/** Cumulative net by the day each trade closed. */
export function curve(trades: OptbtTrade[]): [string, number][] {
  const byDay = new Map<string, number>();
  for (const t of trades) {
    if (!t.closed) continue;
    const d = t.closed.slice(0, 10);
    byDay.set(d, (byDay.get(d) ?? 0) + t.net);
  }
  let total = 0;
  return [...byDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([d, n]) => [d, (total += n)] as [string, number]);
}

export function byMonth(trades: OptbtTrade[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const t of trades) {
    if (!t.closed) continue;
    const m = t.tags?.month ?? t.opened.slice(0, 7);
    out[m] = (out[m] ?? 0) + t.net;
  }
  return out;
}

export interface Row {
  key: string;
  label: string;
  trades: number;
  wins: number;
  net: number;
}

/** Trades grouped by one property, for the breakdown tables. */
export function breakdown(
  trades: OptbtTrade[],
  keyOf: (t: OptbtTrade) => string,
  order?: readonly string[],
): Row[] {
  const rows = new Map<string, Row>();
  for (const t of trades) {
    if (!t.closed) continue;
    const key = keyOf(t);
    const r = rows.get(key) ?? { key, label: key, trades: 0, wins: 0, net: 0 };
    r.trades += 1;
    r.wins += t.net > 0 ? 1 : 0;
    r.net += t.net;
    rows.set(key, r);
  }
  const all = [...rows.values()];
  if (order) all.sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key));
  return all;
}

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"] as const;

export function dteBucket(t: OptbtTrade): string {
  const d = t.tags?.dte ?? -1;
  return d < 0 ? "?" : d >= 5 ? "5+" : String(d);
}
export const DTE_ORDER = ["0", "1", "2", "3", "4", "5+", "?"] as const;

export function vixBucket(t: OptbtTrade): string {
  const p = t.tags?.vix_pct;
  if (p === null || p === undefined) return "unknown";
  const lo = Math.min(80, Math.floor(p / 20) * 20);
  return `${lo}–${lo + 20}`;
}
export const VIX_ORDER = ["0–20", "20–40", "40–60", "60–80", "80–100", "unknown"] as const;

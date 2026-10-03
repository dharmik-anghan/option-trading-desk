/** What price and open interest did together over one bar. */
export type Buildup = "long-buildup" | "short-buildup" | "short-covering" | "long-unwinding";

export const BUILDUP_LABEL: Record<Buildup, string> = {
  "long-buildup": "Long buildup",
  "short-buildup": "Short buildup",
  "short-covering": "Short covering",
  "long-unwinding": "Long unwinding",
};

/**
 * The classic reading. OI up means new positions: with price up they are longs
 * being built, with price down shorts. OI down means positions closing: with
 * price up shorts covering, with price down longs letting go. Null when either
 * did not move, which says nothing.
 */
export function buildup(priceChange: number, oiChange: number): Buildup | null {
  if (priceChange === 0 || oiChange === 0) return null;
  if (oiChange > 0) return priceChange > 0 ? "long-buildup" : "short-buildup";
  return priceChange > 0 ? "short-covering" : "long-unwinding";
}

export interface OiAtBar {
  oi: number;
  /** OI change since the bar before, null on the first and on a roll. */
  change: number | null;
  kind: Buildup | null;
  /** The near month expired on this bar; its change is the expiry. */
  roll: boolean;
}



/**
 * Futures OI laid onto a chart's bars: for each bar, the last reading taken
 * before the next bar opened. OI is a level, so the reading at a bar's end is
 * the bar's. A reading is used by one bar only - a bar with no new reading has
 * none, rather than the last one carried across a gap and read as "no change".
 * Readings are not required to fall after the bar's own opening time, because
 * two sources can stamp the same day differently (midnight, or the open).
 *
 * `starts` are the bars' opening times in ms, ascending; `points` likewise.
 */
export function alignOi(
  starts: readonly number[],
  points: readonly { at: string; close: number; oi: number; roll?: boolean }[],
): (OiAtBar | null)[] {
  const parsed = points
    .map((p) => ({ t: Date.parse(p.at), close: p.close, oi: p.oi, roll: p.roll ?? false }))
    .filter((p) => !Number.isNaN(p.t))
    .sort((a, b) => a.t - b.t);
  // Two sources can stamp the same bar differently - midnight, or the 09:15
  // open - so a reading within half a bar of the next bar's start belongs to
  // that bar. At most half a day, so a weekly bar still takes Friday's reading.
  let step = Infinity;
  for (let i = 1; i < starts.length; i += 1) step = Math.min(step, starts[i] - starts[i - 1]);
  const skew = Number.isFinite(step) ? Math.min(step, 86_400_000) / 2 : 0;
  const out: (OiAtBar | null)[] = [];
  let j = 0;
  let prev: { close: number; oi: number } | null = null;
  for (let i = 0; i < starts.length; i += 1) {
    const end = i + 1 < starts.length ? starts[i + 1] - skew : Infinity;
    let last: { close: number; oi: number } | null = null;
    let rolled = false;
    while (j < parsed.length && parsed[j].t < end) {
      last = parsed[j];
      rolled ||= parsed[j].roll;
      j += 1;
    }
    if (last === null) {
      out.push(null);
      continue;
    }
    // The server flags the bar the near month expired on: what was left in it
    // settles and disappears, so that change is the expiry, not positions.
    const roll = rolled;
    const change = roll || !prev ? null : last.oi - prev.oi;
    const kind = prev && change !== null ? buildup(last.close - prev.close, change) : null;
    out.push({ oi: last.oi, change, kind, roll });
    prev = last;
  }
  return out;
}

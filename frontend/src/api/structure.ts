import { getJson } from "./http";

export interface Swing {
  at: string;
  kind: "high" | "low";
  price: number;
  /** False until k bars have printed after it — the next bar can revoke it. */
  confirmed: boolean;
}

export interface StructureBreak {
  /** When the level was set. A break is a span, not a level. */
  from_at: string;
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
  /** Every close through a swing level in this window, oldest first. */
  breaks: StructureBreak[];
  note: string;
}

export interface MarketStructure {
  underlying: string;
  /** Whose bars were read. Structure is a way of reading bars, not a property
      of one venue, so this says which. */
  source: string;
  name: string;
  k: number;
  /** Bars each reading looks back over, the same count at every size. The chart
      asks `/api/chart` for this many, so what is on screen is what the reading
      was taken from. */
  lookback: number;
  frames: StructureFrame[];
  /** Where every size agrees, if they do. */
  agreement: string;
  caveats: string[];
}

/**
 * How the price has been behaving, at several sizes at once.
 *
 * `sizes` are the ones the chart is offering and `bars` is how many of them it
 * is showing, so the reading describes what is on screen rather than a window
 * of its own choosing.
 */
export function getStructure(
  source: string,
  symbol: string,
  k: number,
  sizes: readonly string[],
  bars: number,
): Promise<MarketStructure> {
  const query = new URLSearchParams({
    k: String(k),
    sizes: sizes.join(","),
    bars: String(bars),
  });
  return getJson<MarketStructure>(
    `/api/structure/${encodeURIComponent(source)}/${encodeURIComponent(symbol)}?${query}`,
  );
}

// --- Pre-open auction ------------------------------------------------------

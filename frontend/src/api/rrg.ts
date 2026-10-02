import { getJson } from "./http";

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
  /** The last session the benchmark's stored bars reach (IST date). */
  bars_to: string | null;
  updater_running: boolean;
  updater_error: string | null;
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

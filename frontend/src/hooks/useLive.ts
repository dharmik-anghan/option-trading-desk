import { useCallback, useEffect, useRef, useState } from "react";

export interface Live<T> {
  data: T | null;
  /** The failure itself, so callers can branch on its kind, not its wording. */
  error: Error | null;
  loading: boolean;
  /** Wall-clock time of the last successful fetch. */
  at: number | null;
  refresh: () => void;
}

/**
 * Poll an endpoint on an interval.
 *
 * There is no websocket on the backend, so the desk polls. Two rules keep
 * that honest: a failed poll never blanks data that is already on screen
 * (you keep the last good figures, and the toolbar shows the feed is stale),
 * and a response that arrives after its request was superseded is dropped,
 * so switching underlying can't repaint the panel with the old one's data.
 */
export function useLive<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  deps: readonly unknown[],
  paused = false,
  /** Delay before the first fetch, to keep panels from firing in one burst. */
  staggerMs = 0,
): Live<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [at, setAt] = useState<number | null>(null);
  const [nonce, setNonce] = useState(0);
  const seq = useRef(0);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  // deps are the caller's; the hook re-subscribes when they change
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  // A stable identity for the caller's deps, so a paused/resumed toggle can be
  // told apart from an actual change of what is being fetched.
  const depsKey = JSON.stringify(deps);

  // Only a real change of target invalidates what is on screen. Pausing must
  // not blank the panels, and resuming must not make them flash empty. Keyed
  // state rather than a ref compared during render, so this is one render.
  const [shown, setShown] = useState(depsKey);
  if (shown !== depsKey) {
    setShown(depsKey);
    setData(null);
    setAt(null);
    setLoading(true);
  }

  useEffect(() => {
    let stopped = false;
    const mine = ++seq.current;

    // Paused means no traffic at all - not even the one initial fetch. A panel
    // that is closed polls nothing, which is the point of closing it.
    if (paused) {
      return () => {
        stopped = true;
      };
    }

    const run = async () => {
      try {
        const next = await fetcherRef.current();
        if (stopped || seq.current !== mine) return;
        setData(next);
        setError(null);
        setAt(Date.now());
      } catch (e) {
        if (stopped || seq.current !== mine) return;
        setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        if (!stopped && seq.current === mine) setLoading(false);
      }
    };

    // Staggered rather than all at once. Every panel starting together put
    // four-plus requests in the same instant, which breached the broker's
    // per-second cap even though the per-minute rate was well inside it.
    let interval: ReturnType<typeof setInterval> | undefined;
    const start = setTimeout(() => {
      void run();
      if (intervalMs > 0) interval = setInterval(run, intervalMs);
    }, staggerMs);

    return () => {
      stopped = true;
      clearTimeout(start);
      if (interval !== undefined) clearInterval(interval);
    };
  }, [depsKey, intervalMs, paused, nonce, staggerMs]);

  return { data, error, loading: loading && !paused, at, refresh };
}

/** Ticks once a second so "updated 4s ago" stays truthful without re-fetching. */
export function useNow(active = true): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

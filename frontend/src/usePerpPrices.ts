import { useEffect, useRef, useState } from "react";
import { API_BASE } from "./api";

export interface StreamedPrice {
  price: number;
  change_pct: number | null;
  /** When we received it, for deciding whether the stream has gone quiet. */
  at: number;
}

/**
 * Prices pushed from the backend rather than asked for.
 *
 * The socket to the exchange was already there; this is the missing half. A tick
 * reached the backend's hub and then sat until the page polled for it, which is
 * why `/api/perps` was being called every two seconds - the prices were live and
 * the browser was not.
 *
 * `EventSource` rather than a websocket: the traffic is one-way, and the browser
 * reconnects a dropped EventSource by itself. A websocket would mean a protocol
 * upgrade and reconnection logic of our own for the same result.
 */
export function usePerpPrices(enabled: boolean): {
  prices: Record<string, StreamedPrice>;
  connected: boolean;
} {
  const [prices, setPrices] = useState<Record<string, StreamedPrice>>({});
  const [connected, setConnected] = useState(false);
  // Held in a ref as well, because the handler is installed once and must not
  // close over a stale copy of the map.
  const latest = useRef<Record<string, StreamedPrice>>({});

  useEffect(() => {
    // No setState here: being disabled is derived below rather than recorded,
    // because writing state in an effect to represent "we did not do anything"
    // starts a render for nothing.
    if (!enabled) return;
    const source = new EventSource(`${API_BASE}/api/perps/stream`);

    source.onopen = () => setConnected(true);

    source.onmessage = (event) => {
      try {
        const tick = JSON.parse(event.data) as {
          symbol?: string;
          price?: number;
          change_pct?: number | null;
        };
        if (!tick.symbol || typeof tick.price !== "number") return;
        latest.current = {
          ...latest.current,
          [tick.symbol]: {
            price: tick.price,
            change_pct: tick.change_pct ?? latest.current[tick.symbol]?.change_pct ?? null,
            at: Date.now(),
          },
        };
        setPrices(latest.current);
      } catch {
        // One malformed frame costs one frame, not the connection.
      }
    };

    source.onerror = () => {
      // EventSource retries by itself; this only records that it is not currently
      // delivering, so the desk can say prices are stale rather than show them as
      // live.
      setConnected(false);
    };

    return () => {
      source.close();
      setConnected(false);
    };
  }, [enabled]);

  // Disabled is not connected, whatever the last connection did before it closed.
  return { prices, connected: enabled && connected };
}

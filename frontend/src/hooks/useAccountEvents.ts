import { useEffect, useRef, useState } from "react";
import { API_BASE } from "../api";

/** Events arriving this close together are one change: a fill brings an order,
    a trade and a position update within a moment of each other. */
const SETTLE_MS = 400;

/**
 * Word from the broker that the account changed - an order, a fill, a
 * position - so the page reads it again then, rather than every few seconds in
 * case it did. `live` is whether the venue's account socket is up; while it is
 * not, the page should keep polling.
 */
export function useAccountEvents(enabled: boolean, onChange: () => void): { live: boolean } {
  const [live, setLive] = useState(false);
  const callback = useRef(onChange);
  useEffect(() => {
    callback.current = onChange;
  }, [onChange]);

  useEffect(() => {
    if (!enabled) return;
    const source = new EventSource(`${API_BASE}/api/portfolio/stream`);
    let settle: ReturnType<typeof setTimeout> | undefined;

    source.addEventListener("ready", (event) => {
      try {
        setLive(Boolean((JSON.parse((event as MessageEvent).data) as { live?: boolean }).live));
      } catch {
        setLive(false);
      }
    });
    source.onmessage = () => {
      clearTimeout(settle);
      settle = setTimeout(() => callback.current(), SETTLE_MS);
    };
    source.onerror = () => setLive(false);

    return () => {
      clearTimeout(settle);
      source.close();
      setLive(false);
    };
  }, [enabled]);

  return { live: enabled && live };
}

import { useLayoutEffect, useRef, useState } from "react";

/**
 * The rendered width of an element, kept current.
 *
 * Charts here draw in real pixels rather than stretching a fixed viewBox, so text
 * is never squashed and a hover maps to the minute under the pointer exactly.
 */
export function useWidth<T extends HTMLElement>(fallback = 900) {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Measured before the first paint, so the chart is never drawn at a guess.
    setWidth(Math.max(280, Math.floor(el.getBoundingClientRect().width)));
    const observer = new ResizeObserver(([entry]) => {
      setWidth(Math.max(280, Math.floor(entry.contentRect.width)));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

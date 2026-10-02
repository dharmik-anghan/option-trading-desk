import { useEffect, useState } from "react";

export type Theme = "dark" | "light";

const KEY = "optiondesk-theme";

function initial(): Theme {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "dark" || saved === "light") return saved;
  } catch {
    // storage can throw in a private window; fall through to the media query
  }
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

/**
 * Light or dark, for the whole site.
 *
 * Held above the router rather than inside a desk, because it is a property of
 * the window and not of a page: with it inside the desk, the home page rendered
 * dark while the desk you had just left was light.
 */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setTheme] = useState<Theme>(initial);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      // not being able to remember the theme is not worth failing over
    }
  }, [theme]);

  return { theme, toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")) };
}

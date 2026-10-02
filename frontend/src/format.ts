// Fyers' unrealized P&L (and sums of it) frequently carry floating-point
// noise (e.g. 942.5000000000017), which is a data artifact of float math,
// not a real number of paise. Round for display everywhere.

const MINUS = "−"; // true minus, so figures align in tabular columns

export function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

/** Plain number with Indian digit grouping. */
export function num(value: number, dp = 2): string {
  if (!Number.isFinite(value)) return "—";
  // A true minus, not the ASCII hyphen toLocaleString gives, so a negative
  // gamma lines up with the signed figures beside it instead of sitting a
  // pixel high and short.
  const body = Math.abs(value).toLocaleString("en-IN", {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  });
  return (value < 0 ? MINUS : "") + body;
}

export function int(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return Math.round(value).toLocaleString("en-IN");
}

/**
 * Signed, for anything that can go either way. Zero carries no sign.
 *
 * Rounded to the decimals being asked for, not to two and then formatted to
 * however many. It rounded to two first, so `signed(0.0013, 4)` printed
 * "0.0000" — which it did on the perpetuals desk, where a 0.002 lot of gold
 * has a profit in the fourth decimal of a dollar and the screen read zero
 * against the venue's 0.06.
 */
export function signed(value: number, dp = 0): string {
  if (!Number.isFinite(value)) return "—";
  const step = 10 ** dp;
  const r = Math.round(value * step) / step;
  const body = dp > 0 ? num(Math.abs(r), dp) : int(Math.abs(r));
  // Exactly zero after rounding carries no sign: "+0.00" claims a gain that
  // the figure beside it does not show.
  if (r === 0) return body;
  return (r > 0 ? "+" : MINUS) + body;
}

/** Lakh/crore compaction — how these numbers are actually spoken here. */
export function compact(value: number): string {
  if (!Number.isFinite(value)) return "—";
  const a = Math.abs(value);
  const s = value < 0 ? MINUS : "";
  if (a >= 1e7) return `${s}${(a / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `${s}${(a / 1e5).toFixed(2)} L`;
  if (a >= 1e3) return `${s}${(a / 1e3).toFixed(1)}k`;
  return s + Math.round(a).toString();
}

export function rupees(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "Unlimited";
  const r = round2(value);
  return (r < 0 ? MINUS : "") + "₹" + Math.round(Math.abs(r)).toLocaleString("en-IN");
}

/** Compact rupees, for card metrics where the column is narrow. */
export function rupeesC(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "Unlimited";
  const r = round2(value);
  return (r < 0 ? MINUS : "") + "₹" + compact(Math.abs(r));
}

/** Rupees with an explicit sign, for a P&L: "+₹1,234". */
export function signedRupees(value: number): string {
  return value > 0 ? `+${rupees(value)}` : rupees(value);
}

export function pct(value: number, dp = 2): string {
  if (!Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? MINUS : "";
  return `${sign}${Math.abs(value * 100).toFixed(dp)}%`;
}

/** "up" / "dn" / "" — the only place a P&L colour is chosen. */
export function dir(value: number): string {
  if (!Number.isFinite(value) || Math.abs(value) < 0.005) return "";
  return value > 0 ? "up" : "dn";
}

/** A premium: two decimals, as the exchange quotes it. */
export function premium(value: number | null): string {
  return value === null ? "—" : value.toFixed(2);
}

/** "2026-09-21T09:20:00" -> "09:20". For times already in exchange time. */
export function hhmm(at: string | null): string {
  return at ? at.slice(11, 16) : "—";
}

/** "2026-09-21T09:20:00" -> "21 Sep 26". For dates already in exchange time. */
export function day(at: string): string {
  const d = new Date(`${at.slice(0, 10)}T00:00:00`);
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit" });
}

export function clockIST(iso: string | number | Date): string {
  const d = new Date(iso);
  return d.toLocaleTimeString("en-GB", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function dayIST(iso: string | number | Date): string {
  const d = new Date(iso);
  const day = d.toLocaleDateString("en-GB", { timeZone: "Asia/Kolkata", day: "2-digit" });
  // en-IN renders September as "Sept"; force the three-letter form.
  const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][
    Number(d.toLocaleDateString("en-GB", { timeZone: "Asia/Kolkata", month: "numeric" })) - 1
  ];
  return `${day} ${mon}`;
}

/** "NSE:NIFTY26OCT22500PE" -> { strike: 22500, optionType: "PE" } */
export function parseContract(
  symbol: string,
): { strike: number; optionType: "CE" | "PE" } | null {
  const m = /(\d+(?:\.\d+)?)(CE|PE)$/.exec(symbol);
  if (!m) return null;
  return { strike: Number(m[1]), optionType: m[2] as "CE" | "PE" };
}

/**
 * How open interest is being used, read from the two day changes together.
 *
 *              price up            price down
 *  OI up       long buildup        short buildup      (positions opening)
 *  OI down     short covering      long unwinding     (positions closing)
 *
 * Neither change alone says anything: open interest rising only tells you
 * positions are being opened, and the price tells you on which side. Returns
 * null when either change is zero, because then there is nothing to read.
 */
export type Buildup = "long-buildup" | "short-buildup" | "short-covering" | "long-unwinding";

export function buildup(ltpChange: number, oiChange: number): Buildup | null {
  if (!ltpChange || !oiChange) return null;
  if (oiChange > 0) return ltpChange > 0 ? "long-buildup" : "short-buildup";
  return ltpChange > 0 ? "short-covering" : "long-unwinding";
}

export const BUILDUP_LABEL: Record<Buildup, string> = {
  "long-buildup": "Long buildup",
  "short-buildup": "Short buildup",
  "short-covering": "Short covering",
  "long-unwinding": "Long unwinding",
};

/** Whether this buildup is positions opening (true) or closing (false). */
export function isOpening(b: Buildup): boolean {
  return b === "long-buildup" || b === "short-buildup";
}

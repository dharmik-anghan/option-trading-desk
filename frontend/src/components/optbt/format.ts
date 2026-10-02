/** Rupees, grouped the Indian way, no paise: 1,23,456. */
export const rupees = (v: number) =>
  `${v < 0 ? "−" : ""}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;

/** Rupees with an explicit sign, for a P&L. */
export const signed = (v: number) => (v > 0 ? `+${rupees(v)}` : rupees(v));


/** A premium: two decimals, as the exchange quotes it. */
export const premium = (v: number | null) => (v === null ? "—" : v.toFixed(2));

/** "2026-09-21T09:20:00" -> "09:20". */
export const hhmm = (at: string | null) => (at ? at.slice(11, 16) : "—");

/** "2026-09-21T09:20:00" -> "21 Sep 26". */
export function day(at: string): string {
  const d = new Date(`${at.slice(0, 10)}T00:00:00`);
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit" });
}

export const tone = (v: number) => (v > 0 ? "up" : v < 0 ? "dn" : "");

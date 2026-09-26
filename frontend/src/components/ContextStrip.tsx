import type { MarketContext } from "../api";
import { int, num } from "../format";

/**
 * The market read, under the toolbar rather than inside the option chain.
 *
 * It belongs here because it describes the underlying, not the chain: it is
 * what you want visible while looking at your positions, and the chain spends
 * most of its life collapsed.
 *
 * A missing figure shows as a dash. None of these is ever zero-by-default —
 * "no open interest anywhere" and "max pain is at 23,000" are different
 * answers and must not look alike.
 */
/**
 * Why the wall figure is not simply the heaviest strike.
 *
 * Round numbers attract open interest whatever the market is doing - on a live
 * NIFTY chain the median at multiples of 1000 was ten times that at multiples
 * of 50 - so the plain maximum mostly reports which strike is roundest. The
 * headline figure is instead the strike unusually heavy for its own kind, and
 * the plain maximum is named here so the two can be compared.
 */
function wallHint(
  prominence: number | null | undefined,
  heaviest: number | null | undefined,
  kind: string,
): string {
  if (prominence == null) return `Where ${kind} open interest concentrates`;
  const strength =
    prominence >= 3 ? "well clear of" : prominence >= 1.5 ? "above" : "barely above";
  const parts = [
    `${num(prominence, 1)}× the typical ${kind} open interest for strikes of its roundness`,
    `— ${strength} normal`,
  ];
  if (heaviest != null) parts.push(`· plain heaviest is ${int(heaviest)}`);
  return parts.join(" ");
}

export function ContextStrip({ context }: { context: MarketContext | null }) {
  const c = context;
  const dash = "—";
  const f = (v: number | null | undefined, dp = 2) => (v == null ? dash : num(v, dp));
  const k = (v: number | null | undefined) => (v == null ? dash : int(v));

  const items: { label: string; value: string; hint?: string; market?: boolean }[] = [
    {
      label: "Futures",
      value: f(c?.futures),
      hint:
        c?.futures_premium == null
          ? c?.futures_symbol ?? undefined
          : `${c.futures_symbol ?? ""} · premium ${num(c.futures_premium, 2)}${
              c.carry_pct == null ? "" : ` · carry ${num(c.carry_pct, 2)}%`
            }`,
    },
    { label: "Put–call ratio", value: f(c?.put_call_ratio), hint: "Total put OI over call OI" },
    {
      label: "Support",
      value: k(c?.support),
      market: true,
      hint: wallHint(c?.support_prominence, c?.support_heaviest, "put"),
    },
    {
      label: "Resistance",
      value: k(c?.resistance),
      market: true,
      hint: wallHint(c?.resistance_prominence, c?.resistance_heaviest, "call"),
    },
    {
      label: "Max pain",
      value: k(c?.max_pain),
      market: true,
      hint: "Where the most open interest expires worthless",
    },
    {
      label: "ATM straddle",
      value: f(c?.atm_straddle),
      hint: c?.atm_strike == null ? undefined : `At the ${int(c.atm_strike)} strike`,
    },
    {
      label: "IV / HV",
      value:
        c?.atm_iv == null || c?.historical_vol == null
          ? dash
          : `${num(c.atm_iv, 1)} / ${num(c.historical_vol, 1)}`,
      hint:
        c?.iv_over_hv == null
          ? "Implied against 20-session historical volatility"
          : `${num(c.iv_over_hv, 2)}× — options cost ${
              c.iv_over_hv >= 1 ? "more" : "less"
            } than the index has lately moved`,
    },
    {
      label: "Skew",
      value: c?.skew == null ? dash : `${num(c.skew, 1)} pts`,
      hint: "Downside implied vol minus upside, about 3% either side",
    },
  ];

  return (
    <div className="context">
      {items.map((i) => (
        <div className={`cx${i.market ? " k" : ""}`} key={i.label} title={i.hint}>
          <i>{i.label}</i>
          <b>{i.value}</b>
        </div>
      ))}
    </div>
  );
}

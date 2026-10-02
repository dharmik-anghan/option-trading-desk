import type { OptbtExpiry, OptbtLegIn } from "../../api";

/** A leg as the form holds it: every field editable, percentages as the user types them. */
export interface LegDraft {
  id: number;
  side: "buy" | "sell";
  kind: "CE" | "PE";
  lots: number;
  expiry: OptbtExpiry;
  expiryDays: number;
  /** "atm" with an offset, "premium" with a target premium, "pct" from spot. */
  strikeMode: "atm" | "premium" | "pct" | "delta";
  offset: number;
  premium: number;
  pct: number;
  delta: number;
  stopKind: "none" | "pct" | "points";
  stopValue: number;
  targetKind: "none" | "pct" | "points";
  targetValue: number;
}

let nextId = 1;

export function leg(
  side: "buy" | "sell",
  kind: "CE" | "PE",
  offset = 0,
  stop: { kind: "none" | "pct" | "points"; value: number } = { kind: "none", value: 25 },
): LegDraft {
  return {
    id: nextId++,
    side,
    kind,
    lots: 1,
    expiry: "week",
    expiryDays: 45,
    strikeMode: "atm",
    offset,
    premium: 50,
    pct: 4,
    delta: 0.3,
    stopKind: stop.kind,
    stopValue: stop.value,
    targetKind: "none",
    targetValue: 50,
  };
}

export function copyLeg(l: LegDraft): LegDraft {
  return { ...l, id: nextId++ };
}

const QUARTER = { kind: "pct" as const, value: 25 };

/** One-click starting points. Each fills in the legs, which stay editable. */
export interface Preset {
  name: string;
  say: string;
  legs: () => LegDraft[];
  /** Settings a preset brings with it beyond its legs. */
  hold?: "intraday" | "expiry";
  targetCredit?: number;
  stopCredit?: number;
  adjust?: boolean;
  equalWings?: boolean;
  exitDte?: number;
}

export const PRESETS: Preset[] = [
  {
    name: "Short straddle",
    say: "sell ATM CE + PE, 25% stop each",
    legs: () => [leg("sell", "CE", 0, QUARTER), leg("sell", "PE", 0, QUARTER)],
  },
  {
    name: "Short strangle",
    say: "sell OTM 2 CE + PE, 25% stop each",
    legs: () => [leg("sell", "CE", 2, QUARTER), leg("sell", "PE", 2, QUARTER)],
  },
  {
    name: "Iron condor",
    say: "sell OTM 4, buy OTM 8, both sides",
    legs: () => [leg("sell", "CE", 4), leg("buy", "CE", 8), leg("sell", "PE", 4), leg("buy", "PE", 8)],
  },
  {
    name: "45 DTE condor",
    say: "Monthly nearest 45 days: sell 0.30 delta, buy 0.17 delta, wings made equal. Positional; out at 50% of the credit, a loss equal to it, or 15 days to expiry. Moves the untested spread in at a wing.",
    hold: "expiry",
    targetCredit: 50,
    stopCredit: 100,
    adjust: true,
    equalWings: true,
    exitDte: 15,
    legs: () =>
      (
        [
          ["sell", "CE", 0.3],
          ["buy", "CE", 0.17],
          ["sell", "PE", 0.3],
          ["buy", "PE", 0.17],
        ] as const
      ).map(([side, kind, delta]) => ({
        ...leg(side, kind),
        expiry: "days" as const,
        expiryDays: 45,
        strikeMode: "delta" as const,
        delta,
      })),
  },
  {
    name: "Iron fly",
    say: "sell ATM, buy OTM 4, both sides",
    legs: () => [leg("sell", "CE", 0), leg("buy", "CE", 4), leg("sell", "PE", 0), leg("buy", "PE", 4)],
  },
  {
    name: "Bull put spread",
    say: "sell OTM 1 PE, buy OTM 5 PE",
    legs: () => [leg("sell", "PE", 1), leg("buy", "PE", 5)],
  },
  {
    name: "Bear call spread",
    say: "sell OTM 1 CE, buy OTM 5 CE",
    legs: () => [leg("sell", "CE", 1), leg("buy", "CE", 5)],
  },
];

export function toRequest(l: LegDraft): OptbtLegIn {
  const level = (kind: "none" | "pct" | "points", value: number) =>
    kind === "none" ? null : { kind, value: kind === "pct" ? value / 100 : value };
  return {
    side: l.side,
    kind: l.kind,
    lots: l.lots,
    expiry: l.expiry,
    expiry_days: l.expiryDays,
    strike: { mode: l.strikeMode, offset: l.offset, premium: l.premium, pct: l.pct, delta: l.delta },
    stop: level(l.stopKind, l.stopValue),
    target: level(l.targetKind, l.targetValue),
  };
}

/** "OTM 2", "ATM", "ITM 1", "₹50 premium". */
export function strikeLabel(
  l: Pick<LegDraft, "strikeMode" | "offset" | "premium" | "pct" | "delta">,
): string {
  if (l.strikeMode === "delta") return `${l.delta.toFixed(2)} delta`;
  if (l.strikeMode === "premium") return `premium ≈ ₹${l.premium}`;
  if (l.strikeMode === "pct") return `${l.pct}% ${l.pct >= 0 ? "OTM" : "ITM"}`;
  if (l.offset === 0) return "ATM";
  return l.offset > 0 ? `OTM ${l.offset}` : `ITM ${-l.offset}`;
}

/** One line a leg can be recognised by: "SELL CE OTM 2 · 25% SL". */
export function legSummary(l: LegDraft): string {
  const parts = [`${l.side.toUpperCase()} ${l.lots > 1 ? `${l.lots}× ` : ""}${l.kind}`, strikeLabel(l)];
  if (l.expiry === "days") parts.push(`${l.expiryDays} DTE`);
  else if (l.expiry !== "week") parts.push(EXPIRY_LABEL[l.expiry].toLowerCase());
  if (l.stopKind !== "none") parts.push(`SL ${l.stopValue}${l.stopKind === "pct" ? "%" : " pt"}`);
  if (l.targetKind !== "none")
    parts.push(`TP ${l.targetValue}${l.targetKind === "pct" ? "%" : " pt"}`);
  return parts.join(" · ");
}

export const EXPIRY_LABEL: Record<OptbtExpiry, string> = {
  week: "This week",
  next_week: "Next week",
  month: "This month",
  next_month: "Next month",
  days: "Days out…",
};

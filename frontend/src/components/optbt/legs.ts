import type { OptbtExpiryChoice, OptbtLegIn } from "../../api";

/** Which of a series: counted nearest first. */
export const NTH = ["1st", "2nd", "3rd"];

/** The nearest weekly: what a strategy trades unless told otherwise. */
export const NEAREST_WEEKLY: OptbtExpiryChoice = { series: "weekly", nth: 1, min_left: 0, days: 45 };

/** A leg as the form holds it: every field editable, percentages as the user types them. */
export interface LegDraft {
  id: number;
  side: "buy" | "sell";
  kind: "CE" | "PE";
  lots: number;
  /** 0 trades the strategy's expiry; 1-3 this leg's own nth of the same series. */
  expiryNth: number;
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
    expiryNth: 0,
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
  expiry?: OptbtExpiryChoice;
  /** Enter only at this many calendar days to expiry, inclusive. */
  dte?: [number, number];
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
    say: "Monthly, entered at about 41 days to expiry (40-42): sell 0.30 delta, buy 0.17 delta, wings made equal. Positional; out at 50% of the credit, a loss equal to it, or 15 days to expiry. Moves the untested spread in at a wing.",
    hold: "expiry",
    expiry: { series: "days", nth: 1, min_left: 0, days: 45 },
    dte: [40, 42],
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

export function toRequest(l: LegDraft, expiry: OptbtExpiryChoice): OptbtLegIn {
  const level = (kind: "none" | "pct" | "points", value: number) =>
    kind === "none" ? null : { kind, value: kind === "pct" ? value / 100 : value };
  return {
    side: l.side,
    kind: l.kind,
    lots: l.lots,
    expiry: l.expiryNth && expiry.series !== "days" ? { ...expiry, nth: l.expiryNth } : null,
    strike: { mode: l.strikeMode, offset: l.offset, premium: l.premium, pct: l.pct, delta: l.delta },
    stop: level(l.stopKind, l.stopValue),
    target: level(l.targetKind, l.targetValue),
  };
}


import { describe, expect, it } from "vitest";
import type { OptbtTrade } from "../../api";
import { NO_FILTER, apply, breakdown, byMonth, curve, figures, moreFigures } from "./explore";

function trade(
  id: number,
  day: string,
  net: number,
  tags: Partial<OptbtTrade["tags"]> = {},
): OptbtTrade {
  return {
    id,
    opened: `${day}T09:20:00`,
    closed: `${day}T15:15:00`,
    ended: net > 0 ? "time+time" : "stop+time",
    gross: net + 100,
    charges: 100,
    net,
    legs: [],
    events: [],
    worst: Math.min(0, net),
    best: Math.max(0, net),
    tags: {
      weekday: "Mon",
      month: day.slice(0, 7),
      dte: 1,
      sessions_to_expiry: 1,
      expiry_day: false,
      monthly_expiry: false,
      spot: 24000,
      vix: 14,
      vix_pct: 50,
      gap_pct: 0.1,
      open_zone: "P-R1",
      ...tags,
    },
  };
}

const RUN = [
  trade(1, "2026-01-05", 1000, { weekday: "Mon" }),
  trade(2, "2026-01-06", -400, { weekday: "Tue", dte: 0, expiry_day: true, vix_pct: 80 }),
  trade(3, "2026-02-02", 300, { weekday: "Mon", open_zone: "below S2" }),
  trade(4, "2026-02-03", -900, { weekday: "Tue", dte: 0, expiry_day: true, vix_pct: 90 }),
];

describe("the result explorer", () => {
  it("filters by weekday, expiry day, VIX percentile, zone and month", () => {
    expect(apply(RUN, { ...NO_FILTER, weekdays: ["Tue"] }).map((t) => t.id)).toEqual([2, 4]);
    expect(apply(RUN, { ...NO_FILTER, expiry: "skip" }).map((t) => t.id)).toEqual([1, 3]);
    expect(apply(RUN, { ...NO_FILTER, vix: "80–100" }).map((t) => t.id)).toEqual([2, 4]);
    expect(apply(RUN, { ...NO_FILTER, dte: "0" }).map((t) => t.id)).toEqual([2, 4]);
    expect(apply(RUN, { ...NO_FILTER, opened: "inside" }).map((t) => t.id)).toEqual([1, 2, 4]);
    expect(apply(RUN, { ...NO_FILTER, opened: "below" }).map((t) => t.id)).toEqual([3]);
    expect(apply(RUN, { ...NO_FILTER, months: ["2026-02"] }).map((t) => t.id)).toEqual([3, 4]);
  });

  it("recomputes the figures from what is left", () => {
    const f = figures(RUN);
    expect(f.net).toBe(0);
    expect(f.wins).toBe(2);
    expect(f.profitFactor).toBeCloseTo(1300 / 1300);
    // 1000, 600, 900, 0: from the 1000 peak the curve fell to 0.
    expect(f.maxDrawdown).toBe(1000);
    expect(curve(RUN).map(([, v]) => v)).toEqual([1000, 600, 900, 0]);
    expect(byMonth(RUN)).toEqual({ "2026-01": 600, "2026-02": -600 });
  });

  it("derives streaks, expectancy and the drawdown's recovery by day", () => {
    const m = moreFigures(RUN);
    // Day nets: +1000, -400, +300, -900 - alternating, so every streak is 1.
    expect(m.maxWinStreak).toBe(1);
    expect(m.maxLossStreak).toBe(1);
    expect(m.tradingDays).toBe(4);
    expect(m.winDays).toBe(2);
    // Win and loss average both 650, half the days each way: net expectancy 0.
    expect(m.expectancy).toBeCloseTo(0);
    // Peaked at 1000 on day 1, fell to 0 on day 4 - never climbs back to 1000.
    expect(m.ddFrom).toBe("2026-01-05");
    expect(m.ddTrough).toBe("2026-02-03");
    expect(m.ddRecovered).toBeNull();
    expect(m.ddRecoveryDays).toBeNull();
    expect(m.returnToMdd).toBe(0);
  });

  it("marks a drawdown recovered once equity matches its peak again", () => {
    const run = [
      trade(1, "2026-01-05", 1000),
      trade(2, "2026-01-06", -600), // trough: cum 400, 600 under the peak
      trade(3, "2026-01-08", 700), // cum 1100: past the 1000 peak - recovered
    ];
    const m = moreFigures(run);
    expect(m.ddTrough).toBe("2026-01-06");
    expect(m.ddRecovered).toBe("2026-01-08");
    expect(m.ddRecoveryDays).toBe(2);
    expect(m.returnToMdd).toBeCloseTo(1100 / 600);
  });

  it("groups trades for the breakdown tables", () => {
    const rows = breakdown(RUN, (t) => t.tags.weekday, ["Mon", "Tue"]);
    expect(rows.map((r) => [r.key, r.trades, r.net])).toEqual([
      ["Mon", 2, 1300],
      ["Tue", 2, -1300],
    ]);
  });

});

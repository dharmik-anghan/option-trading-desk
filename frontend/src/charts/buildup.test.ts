import { describe, expect, it } from "vitest";
import { alignOi, buildup } from "./buildup";

describe("buildup", () => {
  it("reads price and OI together", () => {
    expect(buildup(5, 100)).toBe("long-buildup");
    expect(buildup(-5, 100)).toBe("short-buildup");
    expect(buildup(5, -100)).toBe("short-covering");
    expect(buildup(-5, -100)).toBe("long-unwinding");
    expect(buildup(0, 100)).toBeNull();
  });
});

describe("alignOi", () => {
  const day = (d: number) => Date.UTC(2026, 9, d);
  const at = (d: number, h = 0) => new Date(Date.UTC(2026, 9, d, h)).toISOString();

  it("takes the last reading before the next bar opens", () => {
    const starts = [day(1), day(2), day(3)];
    const points = [
      { at: at(1, 4), close: 100, oi: 10 },
      { at: at(1, 9), close: 101, oi: 12 },
      { at: at(2, 9), close: 99, oi: 15 },
      { at: at(3, 9), close: 103, oi: 13 },
    ];
    const out = alignOi(starts, points);
    expect(out.map((o) => o?.oi)).toEqual([12, 15, 13]);
    expect(out.map((o) => o?.kind)).toEqual([null, "short-buildup", "short-covering"]);
    expect(out[1]?.change).toBe(3);
  });

  it("leaves a bar with no reading empty instead of carrying the last", () => {
    const out = alignOi([day(1), day(2), day(3)], [
      { at: at(1), close: 100, oi: 10 },
      { at: at(3), close: 102, oi: 14 },
    ]);
    expect(out[1]).toBeNull();
    expect(out[2]?.change).toBe(4);
  });

  it("calls a jump to the next contract a roll, not a buildup", () => {
    const out = alignOi([day(1), day(2), day(3)], [
      { at: at(1), close: 100, oi: 60 },
      { at: at(2), close: 101, oi: 180 },
      { at: at(3), close: 102, oi: 185 },
    ]);
    expect(out[1]).toMatchObject({ roll: true, change: null, kind: null });
    expect(out[2]).toMatchObject({ roll: false, change: 5, kind: "long-buildup" });
  });

  it("accepts a reading stamped before the bar's own open on the same day", () => {
    // Index bar at 09:15 IST (03:45 UTC), future stamped at midnight UTC.
    const starts = [Date.UTC(2026, 9, 1, 3, 45), Date.UTC(2026, 9, 2, 3, 45)];
    const out = alignOi(starts, [
      { at: at(1), close: 100, oi: 10 },
      { at: at(2), close: 101, oi: 11 },
    ]);
    expect(out.map((o) => o?.oi)).toEqual([10, 11]);
  });
});

import { describe, expect, it } from "vitest";
import { compact, int, rupees, rupeesC } from "./format";
import { affectsIndia } from "./api";
import type { CalendarEvent } from "./api";

/**
 * The wording of money, pinned.
 *
 * These strings are no longer only ours: the backend raises the same alerts and
 * has to word them identically, or one situation reads as two. `alerting/format.py`
 * asserts this exact table, so a change on either side fails one of the two
 * suites rather than quietly producing two spellings of the same number.
 *
 * The rounding cases at the end are the ones that caught a real difference:
 * JavaScript's Math.round rounds half toward +Infinity, so -2.5 is -2. Python's
 * round is banker's rounding and "away from zero" gives -3; both are wrong here.
 */
const TABLE: [number, string, string, string, string][] = [
  // value,     rupees,        rupeesC,     int,            compact
  [0, "₹0", "₹0", "0", "0"],
  [1234, "₹1,234", "₹1.2k", "1,234", "1.2k"],
  [123456, "₹1,23,456", "₹1.23 L", "1,23,456", "1.23 L"],
  [12345678, "₹1,23,45,678", "₹1.23 Cr", "1,23,45,678", "1.23 Cr"],
  [-2805.4, "−₹2,805", "−₹2.8k", "-2,805", "−2.8k"],
  [15000, "₹15,000", "₹15.0k", "15,000", "15.0k"],
  [-83.15, "−₹83", "−₹83", "-83", "−83"],
  [40000, "₹40,000", "₹40.0k", "40,000", "40.0k"],
  [25000, "₹25,000", "₹25.0k", "25,000", "25.0k"],
  [999.5, "₹1,000", "₹1000", "1,000", "1000"],
  [2.5, "₹3", "₹3", "3", "3"],
  [-2.5, "−₹3", "−₹3", "-2", "−3"],
];

describe("money, worded the way the backend words it", () => {
  it.each(TABLE)("%d", (value, asRupees, asRupeesC, asInt, asCompact) => {
    expect(rupees(value)).toBe(asRupees);
    expect(rupeesC(value)).toBe(asRupeesC);
    expect(int(value)).toBe(asInt);
    expect(compact(value)).toBe(asCompact);
  });

  it("uses a true minus, not a hyphen, so columns align", () => {
    expect(rupees(-100)).toContain("−");
    expect(rupees(-100)).not.toContain("-");
  });

  it("groups digits the Indian way", () => {
    // 1,23,456 rather than 123,456 - the grouping the reader expects
    expect(int(123456)).toBe("1,23,456");
  });

  it("calls an absent number Unlimited rather than zero", () => {
    // a structure with no worst case is unbounded, not break-even
    expect(rupees(null)).toBe("Unlimited");
    expect(rupeesC(null)).toBe("Unlimited");
  });

  it("does not invent a figure from an infinity", () => {
    expect(rupees(Infinity)).toBe("Unlimited");
    expect(int(NaN)).toBe("—");
  });
});

function event(over: Partial<CalendarEvent> = {}): CalendarEvent {
  return {
    day: "2026-10-07",
    name: "RBI Policy Rate",
    label: "RBI Policy Rate",
    importance: "H",
    coverage: "india",
    country: null,
    ...over,
  } as CalendarEvent;
}

describe("which events matter to an Indian index", () => {
  // The backend applies the same rule in alerting/rules.py; this copy only
  // picks the one shown in the top bar.
  it("keeps India and the United States", () => {
    expect(affectsIndia(event())).toBe(true);
    expect(affectsIndia(event({ coverage: "global", country: "United States" }))).toBe(true);
  });

  it("drops everywhere else", () => {
    // filtering on importance alone produced 46 alerts for one position
    expect(affectsIndia(event({ coverage: "global", country: "Thailand" }))).toBe(false);
    expect(affectsIndia(event({ coverage: "global", country: "Brazil" }))).toBe(false);
  });

  it("drops anything less than high importance", () => {
    expect(affectsIndia(event({ importance: "M" }))).toBe(false);
  });
});

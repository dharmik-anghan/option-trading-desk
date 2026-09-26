import { describe, expect, it } from "vitest";
import {
  DEFAULT_LIMITS,
  FIRE_COOLDOWN_MS,
  affectsIndia,
  dedupeLog,
  evaluate,
  eventsBefore,
  reconcile,
} from "./alerts";
import type { Alert, Limits } from "./alerts";
import type {
  Basket,
  BasketLeg,
  CalendarEvent,
  PortfolioResponse,
} from "./api";

const L: Limits = { ...DEFAULT_LIMITS };

function leg(over: Partial<BasketLeg> = {}): BasketLeg {
  return {
    id: 1,
    symbol: "NSE:NIFTY26OCT23800CE",
    option_type: "CE",
    strike: 23800,
    side: "SELL",
    quantity: 65,
    entry_price: 190.3,
    entry_at: "",
    exit_price: null,
    exit_at: null,
    is_open: true,
    ltp: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    iv: null,
    ltp_change: null,
    oi_change: null,
    ...over,
  };
}

function basket(over: Partial<Basket> = {}): Basket {
  return {
    id: 1,
    name: "Iron condor",
    strategy: "iron_condor",
    underlying_symbol: "NSE:NIFTY50-INDEX",
    created_at: "",
    stop_loss: null,
    legs: [leg()],
    max_profit: 1000,
    max_loss: -2000,
    breakevens: [],
    payoff_curve: [],
    payoff_curve_today: [],
    days_to_expiry: 30,
    expiry_date: null,
    single_expiry: true,
    ...over,
  };
}

function pnl(total: number): PortfolioResponse {
  return {
    positions: [],
    realized_pnl: 0,
    unrealized_pnl: total,
    total_pnl: total,
  };
}

const keys = (cs: { key: string }[]) => cs.map((c) => c.key);

function event(over: Partial<CalendarEvent> = {}): CalendarEvent {
  return {
    day: "2026-10-07",
    name: "RBI Policy Rate",
    label: "RBI Policy Rate",
    importance: "H",
    coverage: "india",
    country: null,
    ...over,
  };
}

describe("account limits", () => {
  it("fires the profit target at the limit, not before", () => {
    expect(keys(evaluate(pnl(L.target), null, L))).toContain("target");
    expect(keys(evaluate(pnl(L.target - 1), null, L))).not.toContain("target");
  });

  it("warns at 80% of the daily loss, and only breaches past it", () => {
    const near = keys(evaluate(pnl(-L.dailyLoss * 0.85), null, L));
    expect(near).toContain("daily-loss-near");
    expect(near).not.toContain("daily-loss");

    const past = keys(evaluate(pnl(-L.dailyLoss * 1.2), null, L));
    expect(past).toContain("daily-loss");
    // the breach replaces the warning rather than stacking with it
    expect(past).not.toContain("daily-loss-near");
  });
});

describe("per-structure risk", () => {
  it("flags a worst case beyond the limit", () => {
    expect(keys(evaluate(null, [basket({ max_loss: -99999 })], L))).toContain(
      "worst-case:1",
    );
    expect(keys(evaluate(null, [basket({ max_loss: -100 })], L))).not.toContain(
      "worst-case:1",
    );
  });

  it("flags unlimited downside separately, since no limit can cover it", () => {
    expect(keys(evaluate(null, [basket({ max_loss: null })], L))).toContain(
      "unbounded:1",
    );
  });

  it("warns as expiry approaches", () => {
    expect(keys(evaluate(null, [basket({ days_to_expiry: 2 })], L))).toContain(
      "expiry:1",
    );
    expect(
      keys(evaluate(null, [basket({ days_to_expiry: 20 })], L)),
    ).not.toContain("expiry:1");
  });

  it("says nothing about a fully closed structure", () => {
    const closed = basket({
      legs: [leg({ is_open: false, delta: 0.9 })],
      max_loss: -99999,
    });
    expect(evaluate(null, [closed], L)).toEqual([]);
  });
});

describe("short strikes under pressure", () => {
  it("flags a short whose delta has run up", () => {
    expect(
      keys(evaluate(null, [basket({ legs: [leg({ delta: 0.42 })] })], L)),
    ).toContain("tested:1");
    expect(
      keys(evaluate(null, [basket({ legs: [leg({ delta: 0.12 })] })], L)),
    ).not.toContain("tested:1");
  });

  it("never flags a long leg, however deep it goes", () => {
    const long = basket({ legs: [leg({ side: "BUY", delta: 0.95 })] });
    expect(keys(evaluate(null, [long], L))).not.toContain("tested:1");
  });

  it("reads rising price with rising open interest as long buildup", () => {
    const [c] = evaluate(
      null,
      [basket({ legs: [leg({ ltp_change: 5, oi_change: 1000 })] })],
      L,
    );
    expect(c.key).toBe("buildup:1");
    expect(c.message).toMatch(/Long buildup/);
    // the structure's name is carried apart from the message, not glued on
    expect(c.subject).toBe("Iron condor");
    expect(c.message).not.toMatch(/Iron condor/);
  });

  it("reads rising price with falling open interest as short covering", () => {
    const [c] = evaluate(
      null,
      [basket({ legs: [leg({ ltp_change: 5, oi_change: -1000 })] })],
      L,
    );
    expect(c.message).toMatch(/Short covering/);
  });

  it("stays quiet when the short you sold is falling", () => {
    const easing = basket({ legs: [leg({ ltp_change: -5, oi_change: 1000 })] });
    expect(keys(evaluate(null, [easing], L))).not.toContain("buildup:1");
  });

  it("stays quiet without live figures to read", () => {
    expect(evaluate(null, [basket()], L)).toEqual([]);
  });
});

describe("edge triggering", () => {
  const breach = evaluate(pnl(-L.dailyLoss), null, L);
  // The transition rule, isolated. These timestamps are seconds apart, so the
  // fire cooldown would mask what is being tested here; it has its own tests.
  const NO_COOLDOWN = 0;

  it("writes once when a condition becomes true and stays quiet after", () => {
    let active = new Set<string>();
    let log: Alert[] = [];

    for (const at of [1000, 2000, 3000]) {
      const next = reconcile(active, log, breach, at);
      active = next.active;
      log = next.log;
    }

    // three polls, one line - this is the whole point of the engine
    expect(log).toHaveLength(1);
    expect(log[0].at).toBe(1000);
  });

  it("can fire again once the condition has cleared", () => {
    let r = reconcile(new Set(), [], breach, 1000);
    r = reconcile(r.active, r.log, [], 2000, undefined, undefined, NO_COOLDOWN); // clears
    expect(r.active.size).toBe(0);
    expect(r.log).toHaveLength(1);

    r = reconcile(r.active, r.log, breach, 3000, undefined, undefined, NO_COOLDOWN); // returns
    expect(r.log).toHaveLength(2);
    expect(r.log[0].at).toBe(3000); // newest first
  });

  it("does not re-fire across a reload, given the active set is restored too", () => {
    // first session: the condition fires once
    const first = reconcile(new Set(), [], breach, 1000);
    expect(first.log).toHaveLength(1);

    // a refresh restores both halves of what was saved
    const restoredActive = new Set(first.active);
    const restoredLog = [...first.log];

    const afterReload = reconcile(restoredActive, restoredLog, breach, 2000);

    expect(afterReload.fired).toHaveLength(0);
    expect(afterReload.log).toHaveLength(1);
    expect(afterReload.log[0].at).toBe(1000); // the original time, not the reload's
  });

  it("re-fires across a reload only if the active set was lost", () => {
    // the bug this guards: restoring the log but not the active set made every
    // still-true condition look like a fresh transition on every refresh
    const first = reconcile(new Set(), [], breach, 1000);
    const lostActive = reconcile(new Set(), first.log, breach, 2000, undefined, undefined, NO_COOLDOWN);

    expect(lostActive.fired).toHaveLength(1);
    expect(lostActive.log).toHaveLength(2);
  });

  it("forgets a condition that cleared while the page was closed", () => {
    const first = reconcile(new Set(), [], breach, 1000);
    // reopened, and the loss has recovered - nothing is true any more
    const reopened = reconcile(new Set(first.active), first.log, [], 2000);
    expect(reopened.active.size).toBe(0);
    expect(reopened.log).toHaveLength(1);
  });

  it("treats an empty condition list as everything having cleared", () => {
    // This is reconcile's contract, and the reason callers must not run it
    // until the data it reads has actually arrived: "no conditions" and "no
    // data yet" are indistinguishable here, and calling it early wipes the
    // active set, which re-fires everything on the next pass.
    const first = reconcile(new Set(), [], breach, 1000);
    expect(first.active.size).toBe(1);

    const premature = reconcile(first.active, first.log, [], 1500);
    expect(premature.active.size).toBe(0);

    // and now the same condition counts as new again
    expect(
      reconcile(premature.active, premature.log, breach, 2000, undefined, undefined, NO_COOLDOWN)
        .fired,
    ).toHaveLength(1);
  });

  it("caps the log so a long session cannot grow without bound", () => {
    let log: Alert[] = [];
    let active = new Set<string>();
    for (let i = 0; i < 10; i++) {
      const r = reconcile(active, log, breach, i * 1000, undefined, undefined, NO_COOLDOWN);
      active = new Set(); // force a re-fire each pass
      log = r.log;
    }
    const capped = reconcile(new Set(), log, breach, 99999, 3, undefined, NO_COOLDOWN);
    expect(capped.log).toHaveLength(3);
  });
});

describe("scheduled events inside a position's life", () => {
  const held = basket({ expiry_date: "27-10-2026", days_to_expiry: 31 });

  it("warns about a high-impact release before the expiry", () => {
    const [c] = evaluate(null, [held], L, [event()]).filter((x) =>
      x.key.startsWith("event:"),
    );
    expect(c.message).toMatch(
      /RBI Policy Rate on 2026-10-07 lands before this expires/,
    );
    expect(c.subject).toBe("Iron condor");
  });

  it("ignores events after the expiry", () => {
    const after = [event({ day: "2026-11-20" })];
    expect(
      keys(evaluate(null, [held], L, after)).filter((k) =>
        k.startsWith("event:"),
      ),
    ).toEqual([]);
  });

  it("only warns about releases that move an Indian index", () => {
    // filtering on "high impact" alone gave 46 warnings for one position,
    // including inflation prints from Thailand and Türkiye
    const noise = [
      event({
        day: "2026-10-05",
        name: "Inflation",
        label: "Inflation (Thailand)",
        coverage: "global",
        country: "Thailand",
      }),
      event({
        day: "2026-10-09",
        name: "Inflation",
        label: "Inflation (Brazil)",
        coverage: "global",
        country: "Brazil",
      }),
    ];
    const signal = [
      event(), // RBI, India
      event({
        day: "2026-10-14",
        name: "Inflation",
        label: "Inflation (United States)",
        coverage: "global",
        country: "United States",
      }),
    ];

    expect(eventsBefore(noise, "27-10-2026")).toEqual([]);
    expect(eventsBefore(signal, "27-10-2026")).toHaveLength(2);
  });

  it("classifies what counts as moving India", () => {
    expect(affectsIndia(event())).toBe(true);
    expect(
      affectsIndia(event({ coverage: "global", country: "United States" })),
    ).toBe(true);
    expect(
      affectsIndia(event({ coverage: "global", country: "Thailand" })),
    ).toBe(false);
    // importance still has to clear the bar
    expect(affectsIndia(event({ importance: "M" }))).toBe(false);
  });

  it("ignores medium and low importance", () => {
    const minor = [event({ importance: "M" }), event({ importance: "L" })];
    expect(
      keys(evaluate(null, [held], L, minor)).filter((k) =>
        k.startsWith("event:"),
      ),
    ).toEqual([]);
  });

  it("compares dates as dates, not as text", () => {
    // "07-10-2026" and "12-10-2026" sort correctly as ISO but not as the
    // day-first form the basket carries, which is why one is converted
    const inWindow = eventsBefore([event({ day: "2026-10-12" })], "27-10-2026");
    expect(inWindow).toHaveLength(1);

    const outOfWindow = eventsBefore(
      [event({ day: "2027-01-05" })],
      "27-10-2026",
    );
    expect(outOfWindow).toEqual([]);
  });

  it("says nothing when the expiry is unknown", () => {
    expect(eventsBefore([event()], null)).toEqual([]);
    expect(eventsBefore([event()], "garbage")).toEqual([]);
  });

  it("fires once per event, not once per poll", () => {
    const conditions = evaluate(null, [held], L, [event()]);
    let active = new Set<string>();
    let log: Alert[] = [];
    for (const at of [1000, 2000, 3000]) {
      const next = reconcile(active, log, conditions, at);
      active = next.active;
      log = next.log;
    }
    expect(log.filter((a) => a.key.startsWith("event:"))).toHaveLength(1);
  });
});


describe("repeats a past bug left in the stored log", () => {
  const line = (key: string, at: number): Alert => ({
    key,
    severity: "warn",
    message: "Monthly Non Farm (United States) on 2026-10-02 lands before this expires",
    at,
  });

  it("collapses a near-simultaneous repeat of one key", () => {
    // the signature of the bug: re-fired seconds later when data landed
    const healed = dedupeLog([line("event:1:x", 1_000_000), line("event:1:x", 1_002_400)]);

    expect(healed).toHaveLength(1);
    // the earliest is kept, because that is when it actually became true
    expect(healed[0].at).toBe(1_000_000);
  });

  it("keeps a condition that genuinely cleared and came back", () => {
    const hours = 3 * 3600_000;
    const healed = dedupeLog([line("event:1:x", 1_000_000), line("event:1:x", 1_000_000 + hours)]);

    expect(healed).toHaveLength(2);
  });

  it("leaves different keys alone however close together", () => {
    const healed = dedupeLog([line("tested:4", 1_000_000), line("buildup:2", 1_000_001)]);
    expect(healed).toHaveLength(2);
  });

  it("returns newest first, as the panel shows them", () => {
    const healed = dedupeLog([line("a", 1000), line("b", 3000), line("c", 2000)]);
    expect(healed.map((a) => a.at)).toEqual([3000, 2000, 1000]);
  });

  it("handles an empty log", () => {
    expect(dedupeLog([])).toEqual([]);
  });
});

describe("reconcile does not trust its input", () => {
  it("writes one line even if a key is offered twice", () => {
    const twice = [
      { key: "event:1:x", severity: "warn" as const, message: "once" },
      { key: "event:1:x", severity: "warn" as const, message: "again" },
    ];

    const result = reconcile(new Set(), [], twice, 1000);

    expect(result.fired).toHaveLength(1);
    expect(result.log).toHaveLength(1);
    expect(result.active.size).toBe(1);
  });
});


describe("a source that has not loaded yet", () => {
  const eventCondition = [
    {
      key: "event:1:2026-10-02:Monthly Non Farm",
      severity: "warn" as const,
      message: "Monthly Non Farm (United States) on 2026-10-02 lands before this expires",
    },
  ];
  const notLoaded = (key: string) => !key.startsWith("event:");

  it("does not clear the keys it cannot judge", () => {
    // the calendar polls far more slowly than the portfolio, so evaluation
    // happens in between with no events loaded at all
    const first = reconcile(new Set(), [], eventCondition, 1000);
    expect(first.log).toHaveLength(1);

    const gap = reconcile(first.active, first.log, [], 2000, 200, notLoaded);

    expect(gap.active.has("event:1:2026-10-02:Monthly Non Farm")).toBe(true);
    expect(gap.fired).toHaveLength(0);
  });

  it("so the alert does not fire again when it arrives", () => {
    const first = reconcile(new Set(), [], eventCondition, 1000);
    const gap = reconcile(first.active, first.log, [], 2000, 200, notLoaded);
    const arrived = reconcile(gap.active, gap.log, eventCondition, 3000);

    expect(arrived.fired).toHaveLength(0);
    expect(arrived.log).toHaveLength(1);
  });

  it("without the guard it fires twice, seconds apart", () => {
    // the reported bug, reproduced: this is what the old behaviour did
    // with the cooldown off too, so this shows the guard's own contribution
    const first = reconcile(new Set(), [], eventCondition, 1000, undefined, undefined, 0);
    const gap = reconcile(first.active, first.log, [], 2000, undefined, undefined, 0);
    const arrived = reconcile(gap.active, gap.log, eventCondition, 3400, undefined, undefined, 0);

    expect(arrived.log).toHaveLength(2);
    expect(arrived.log[0].at - arrived.log[1].at).toBeLessThan(60_000);
  });

  it("still clears a key once its source can be judged", () => {
    const first = reconcile(new Set(), [], eventCondition, 1000);
    // events loaded, and the event is no longer in the window
    const cleared = reconcile(first.active, first.log, [], 2000, 200, () => true);

    expect(cleared.active.size).toBe(0);
  });
});

describe("the same warning must not arrive twice", () => {
  // Three batches of identical warnings landed 22 seconds and then 11 minutes
  // apart. Two causes, both real: a short at delta 0.32 against a 0.30
  // threshold crossed back and forth, and each crossing was a true false->true
  // transition; and a calendar that re-scraped empty made every event
  // condition look cleared.
  const tested = (delta: number) =>
    evaluate(null, [basket({ legs: [leg({ id: 19, side: "SELL", delta })] })], L, []);

  it("fires a tested short once, at the threshold", () => {
    expect(keys(tested(-0.32))).toContain("tested:19");
  });

  it("holds the alert on while delta wobbles back under the threshold", () => {
    // Already on, and 0.29 is inside the release band: still tested.
    const on = new Set(["tested:19"]);
    const held = evaluate(
      null,
      [basket({ legs: [leg({ id: 19, side: "SELL", delta: -0.29 })] })],
      L,
      [],
      on,
    );
    expect(keys(held)).toContain("tested:19");
  });

  it("clears once delta falls plainly clear of the threshold", () => {
    const on = new Set(["tested:19"]);
    const gone = evaluate(
      null,
      [basket({ legs: [leg({ id: 19, side: "SELL", delta: -0.2 })] })],
      L,
      [],
      on,
    );
    expect(keys(gone)).not.toContain("tested:19");
  });

  it("does not re-fire a key inside the cooldown, however the condition churned", () => {
    const now = 1_000_000;
    const log: Alert[] = [
      { key: "tested:19", severity: "warn", message: "Short 22,900 PE tested", at: now },
    ];
    // condition went false (active set empty) and came back 11 minutes later,
    // which is what produced the 11:44 and 11:55 pair
    const after = reconcile(new Set(), log, tested(-0.32), now + 11 * 60_000);
    expect(after.fired).toHaveLength(0);
    expect(after.log).toHaveLength(1);
    // but it is active again, so it is not reported as cleared
    expect(after.active.has("tested:19")).toBe(true);
  });

  it("does fire again once the cooldown has passed", () => {
    const now = 1_000_000;
    const log: Alert[] = [
      { key: "tested:19", severity: "warn", message: "Short 22,900 PE tested", at: now },
    ];
    const after = reconcile(new Set(), log, tested(-0.32), now + FIRE_COOLDOWN_MS + 1);
    expect(after.fired).toHaveLength(1);
  });
});

describe("two events on one day", () => {
  // Real data from the calendar: 2026-09-28 carried "Goods Exports (Mexico)"
  // and "Goods Exports (Malaysia)". Keyed on `name` both were "Goods Exports",
  // so the second was silently swallowed as a duplicate of the first.
  const both = [
    event({ day: "2026-09-28", name: "Goods Exports", label: "Goods Exports (Mexico)",
            coverage: "global", country: "United States" }),
    event({ day: "2026-09-28", name: "Goods Exports", label: "Goods Exports (Malaysia)",
            coverage: "global", country: "United States" }),
  ];

  it("warns about each, not just the first", () => {
    const cs = evaluate(null, [basket({ expiry_date: "27-10-2026" })], L, both);
    const events = keys(cs).filter((k) => k.startsWith("event:"));
    expect(new Set(events).size).toBe(2);
  });
});

describe("healing a log an earlier build poisoned", () => {
  it("collapses the eleven-minute repeats that were reported", () => {
    // the shape of the reported log: one batch at 11:44:12, again at 11:44:34,
    // again at 11:55:18
    const base = Date.parse("2026-09-26T11:44:12+05:30");
    const at = (mins: number, secs = 0) => base + mins * 60_000 + secs * 1000;
    const line = (key: string, when: number): Alert => ({
      key,
      severity: "warn",
      message: "Monthly Non Farm (United States) on 2026-10-02 lands before this expires",
      at: when,
    });
    const poisoned = [
      line("event:8:2026-10-02:Monthly Non Farm (United States)", at(11, 6)),
      line("event:8:2026-10-02:Monthly Non Farm (United States)", at(0, 22)),
      line("event:8:2026-10-02:Monthly Non Farm (United States)", at(0)),
    ];
    expect(dedupeLog(poisoned, FIRE_COOLDOWN_MS)).toHaveLength(1);
  });
});

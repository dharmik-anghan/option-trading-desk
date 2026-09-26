import type { Basket, CalendarEvent, PortfolioResponse } from "./api";
import { int, rupees, rupeesC } from "./format";

export type Severity = "risk" | "warn" | "target" | "info";

export interface Alert {
  /** Stable per condition, so the same condition is one alert, not one per poll. */
  key: string;
  severity: Severity;
  /** What it concerns - a structure's name, or absent for account-wide ones. */
  subject?: string;
  /** Kept short. Held apart from `subject` so the panel can lay the two out
      instead of being handed one long sentence to wrap. */
  message: string;
  at: number;
}

export interface Limits {
  /** Net profit at which to say "you're done". */
  target: number;
  /** Net loss at which to stop. Held as a positive magnitude. */
  dailyLoss: number;
  /** Largest acceptable worst-case-at-expiry for one structure. */
  maxLoss: number;
  /** |delta| at which a short strike counts as being tested. */
  shortDelta: number;
  /** Days before expiry to start warning. */
  expiryDays: number;
}

export const DEFAULT_LIMITS: Limits = {
  target: 15000,
  dailyLoss: 25000,
  maxLoss: 40000,
  shortDelta: 0.3,
  expiryDays: 3,
};

/** A condition that is currently true, before it is turned into an alert. */
interface Condition {
  key: string;
  severity: Severity;
  subject?: string;
  message: string;
}

/**
 * Every condition that holds right now.
 *
 * Pure: same inputs, same output, no timestamps and no memory. Turning these
 * into a log is `reconcile`'s job, which is what makes the firing rule easy to
 * state and to trust.
 */
export function evaluate(
  portfolio: PortfolioResponse | null,
  baskets: Basket[] | null,
  limits: Limits,
  events: readonly CalendarEvent[] = [],
  /**
   * Keys currently alerting, used only to widen a threshold that has already
   * tripped. Still pure - same inputs, same output - it just needs to know
   * which side of the band it is on.
   */
  sticky: ReadonlySet<string> = new Set(),
): Condition[] {
  const on: Condition[] = [];

  if (portfolio) {
    const net = portfolio.total_pnl;
    if (net >= limits.target) {
      on.push({
        key: "target",
        severity: "target",
        message: `Profit target reached — net ${rupees(net)}`,
      });
    }
    if (net <= -limits.dailyLoss) {
      on.push({
        key: "daily-loss",
        severity: "risk",
        message: `Daily loss limit breached — net ${rupees(net)}`,
      });
    } else if (net <= -0.8 * limits.dailyLoss) {
      on.push({
        key: "daily-loss-near",
        severity: "warn",
        message: `80% of the daily loss used — net ${rupees(net)}`,
      });
    }
  }

  for (const b of baskets ?? []) {
    const open = b.legs.filter((l) => l.is_open);
    if (!open.length) continue;

    if (b.max_loss === null) {
      on.push({
        key: `unbounded:${b.id}`,
        severity: "risk",
        subject: b.name,
        message: "Unlimited downside — no worst case to check",
      });
    } else if (Math.abs(b.max_loss) > limits.maxLoss) {
      on.push({
        key: `worst-case:${b.id}`,
        severity: "risk",
        subject: b.name,
        message: `Worst case ${rupeesC(b.max_loss)} is past your ${rupeesC(-limits.maxLoss)} limit`,
      });
    }

    if (b.days_to_expiry !== null && b.days_to_expiry <= limits.expiryDays) {
      on.push({
        key: `expiry:${b.id}`,
        severity: "warn",
        subject: b.name,
        message: `Expires in ${b.days_to_expiry.toFixed(1)} days`,
      });
    }

    // A scheduled release inside the life of the position is the one piece of
    // news that certainly bears on it: the structure has to survive that day,
    // and a short-premium book cannot step aside for it.
    for (const e of eventsBefore(events, b.expiry_date)) {
      on.push({
        key: `event:${b.id}:${e.day}:${e.label}`,
        severity: "warn",
        subject: b.name,
        message: `${e.label} on ${e.day} lands before this expires`,
      });
    }

    for (const leg of open) {
      if (leg.side !== "SELL") continue;
      const label = `${int(leg.strike)} ${leg.option_type}`;

      // A band, not a line. A short sitting at delta 0.32 against a 0.30
      // threshold crosses back and forth all session, and each crossing is a
      // real false->true transition, so the alert fired again every time. Once
      // it is on it stays on until delta falls clear of the threshold, which is
      // how a trader reads it anyway: the strike is being tested until it
      // plainly is not.
      const testedKey = `tested:${leg.id}`;
      const onset = limits.shortDelta;
      const release = limits.shortDelta * HYSTERESIS;
      const bound = sticky.has(testedKey) ? release : onset;
      if (leg.delta !== null && Math.abs(leg.delta) >= bound) {
        on.push({
          key: testedKey,
          severity: "warn",
          subject: b.name,
          message: `Short ${label} tested — delta ${Math.abs(leg.delta).toFixed(2)}`,
        });
      }

      // Open interest building, or shorts covering, at a strike you are short
      // is the market moving against that leg specifically.
      if (leg.ltp_change !== null && leg.oi_change !== null && leg.ltp_change > 0) {
        const opening = leg.oi_change > 0;
        if (leg.oi_change !== 0) {
          on.push({
            key: `buildup:${leg.id}`,
            severity: "warn",
            subject: b.name,
            message: `${opening ? "Long buildup" : "Short covering"} at short ${label}`,
          });
        }
      }
    }
  }

  return on;
}

/**
 * Countries whose releases move an Indian index enough to be worth a warning.
 *
 * India obviously, and the United States because a Fed decision or a US CPI
 * print reprices risk everywhere. Everything else is left out on purpose:
 * filtering only by "high impact" produced 46 alerts for one position,
 * including inflation prints from Thailand, Türkiye and Brazil. A warning list
 * that long is one nobody reads, which defeats the point of having one.
 */
const MOVES_INDIA = new Set(["United States"]);

export function affectsIndia(event: CalendarEvent): boolean {
  if (event.importance !== "H") return false;
  return event.coverage === "india" || (event.country !== null && MOVES_INDIA.has(event.country));
}

/**
 * Events between now and an expiry that bear on an Indian index position.
 *
 * The basket carries its expiry as DD-MM-YYYY and the calendar uses ISO, so one
 * is converted rather than compared as text - which would have sorted 07-10
 * before 12-10 and been wrong about the year entirely.
 */
export function eventsBefore(
  events: readonly CalendarEvent[],
  expiryDate: string | null,
): CalendarEvent[] {
  if (!expiryDate) return [];
  const [d, m, y] = expiryDate.split("-");
  if (!d || !m || !y) return [];
  const expiry = `${y}-${m}-${d}`;
  const today = new Date().toISOString().slice(0, 10);
  return events.filter((e) => affectsIndia(e) && e.day >= today && e.day <= expiry);
}

/**
 * Fold the conditions holding now into the log.
 *
 * Edge-triggered: an alert is written when a condition becomes true, and not
 * again while it stays true. Without this, a 5-second poll would write the same
 * "daily loss breached" line twelve times a minute and bury everything else.
 * A condition that clears is forgotten, so it can fire again if it returns.
 */
export function reconcile(
  active: ReadonlySet<string>,
  log: readonly Alert[],
  conditions: readonly Condition[],
  now: number,
  limit = 200,
  /**
   * Whether a key could be judged this pass.
   *
   * Conditions are drawn from several sources that arrive at different times.
   * A source that has not loaded yet produces no conditions, which is
   * indistinguishable from its conditions having cleared - so its keys would
   * be dropped from the active set and re-fire the moment it landed. That is
   * how the same event alert appeared twice seconds apart. Keys reported as
   * not evaluable are carried over untouched instead.
   */
  evaluable: (key: string) => boolean = () => true,
  /** Quiet period after a key fires. See FIRE_COOLDOWN_MS. */
  cooldownMs = FIRE_COOLDOWN_MS,
): { active: Set<string>; log: Alert[]; fired: Alert[] } {
  // One line per condition even if the same key is offered twice. Nothing does
  // that today, but a key is a promise that an alert is written once, and that
  // promise should not depend on the caller being careful.
  const unique = new Map<string, Condition>();
  for (const c of conditions) if (!unique.has(c.key)) unique.set(c.key, c);

  const nowActive = new Set(unique.keys());
  for (const key of active) {
    if (!evaluable(key)) nowActive.add(key);
  }

  // When each key last said something, so a key that has just fired stays
  // quiet even if its condition has genuinely gone false and true again.
  const lastFired = new Map<string, number>();
  for (const entry of log) {
    const seen = lastFired.get(entry.key);
    if (seen === undefined || entry.at > seen) lastFired.set(entry.key, entry.at);
  }
  const cooling = (key: string) => {
    const previous = lastFired.get(key);
    return previous !== undefined && now - previous < cooldownMs;
  };

  const fired = [...unique.values()]
    .filter((c) => !active.has(c.key) && !cooling(c.key))
    .map((c) => ({
      key: c.key,
      severity: c.severity,
      subject: c.subject,
      message: c.message,
      at: now,
    }));
  return {
    active: nowActive,
    log: [...fired, ...log].slice(0, limit),
    fired,
  };
}

export const SEVERITY_LABEL: Record<Severity, string> = {
  risk: "RISK",
  warn: "WARN",
  target: "TARGET",
  info: "INFO",
};


//: Two firings of one key closer together than this were not a condition
//: clearing and returning - nothing clears and returns inside a minute.
const REFIRE_FLOOR_MS = 60_000;

/**
 * How long a key stays quiet after firing.
 *
 * The last line of defence against a repeat. Hysteresis stops the churn we
 * know about; this one bounds the damage from any source we have not thought
 * of, because six identical warnings eleven minutes apart is worse than a
 * missed re-test.
 */
export const FIRE_COOLDOWN_MS = 15 * 60_000;

/** Fraction of a threshold at which an alert that is already on clears. */
const HYSTERESIS = 0.9;

/**
 * Collapse repeats a past bug wrote into a stored log.
 *
 * The log outlives the code that produced it, so a build that double-fired
 * leaves rows behind that no later fix removes - and the desk keeps showing
 * them. An earlier version re-evaluated before the basket data had arrived,
 * which cleared the active set and re-fired everything the instant it landed,
 * seconds after the original.
 *
 * Only near-simultaneous repeats of the same key go. A condition that genuinely
 * cleared and came back is real history and is separated by minutes at least,
 * so it survives. The earliest of a cluster is kept, because that is when the
 * condition actually became true.
 */
export function dedupeLog(log: readonly Alert[], floorMs = REFIRE_FLOOR_MS): Alert[] {
  const oldestFirst = [...log].sort((a, b) => a.at - b.at);
  const kept: Alert[] = [];
  const lastSeen = new Map<string, number>();

  for (const alert of oldestFirst) {
    const previous = lastSeen.get(alert.key);
    if (previous !== undefined && alert.at - previous < floorMs) continue;
    lastSeen.set(alert.key, alert.at);
    kept.push(alert);
  }

  return kept.sort((a, b) => b.at - a.at);
}

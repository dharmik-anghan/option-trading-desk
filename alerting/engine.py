"""Turning conditions into a log, once each.

A port of the frontend's `reconcile`/`dedupeLog`, including every guard the
browser version grew the hard way. Each one exists because of a specific way
duplicate alerts reached the screen:

- edge triggering, so a five-second poll does not write the same line twelve
  times a minute;
- `evaluable`, because conditions come from sources that load at different
  times, and a source that has not answered yet looks exactly like one whose
  conditions have cleared;
- a per-key cooldown, because a condition can genuinely churn - a delta either
  side of a threshold, an open-interest change either side of zero - and each
  crossing is a real transition that nobody wants to read about again.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from alerting.models import Alert, Condition, Outcome

#: Floor used when healing a log that already holds repeats.
REFIRE_FLOOR_MS = 60_000

#: How long a key stays quiet after firing.
#:
#: The last line of defence against a repeat. Hysteresis in `rules.py` stops the
#: churn we know about; this bounds the damage from any source we have not
#: thought of, because six identical warnings eleven minutes apart is worse than
#: a late re-test.
FIRE_COOLDOWN_MS = 15 * 60_000

#: Most alerts kept. A log is a convenience, not an audit trail.
LOG_LIMIT = 200


def reconcile(
    active: frozenset[str],
    log: Sequence[Alert],
    conditions: Sequence[Condition],
    now: int,
    limit: int = LOG_LIMIT,
    evaluable: Callable[[str], bool] | None = None,
    cooldown_ms: int = FIRE_COOLDOWN_MS,
) -> Outcome:
    """Fold the conditions holding now into the log.

    Edge-triggered: an alert is written when a condition becomes true, and not
    again while it stays true. A condition that clears is forgotten, so it can
    fire again if it returns - subject to the cooldown.

    `evaluable` says whether a key could be judged this pass. Keys reported as
    unjudgeable are carried over untouched rather than treated as cleared.
    """
    judgeable = evaluable or (lambda _key: True)

    # One line per condition even if the same key is offered twice. Nothing does
    # that today, but a key is a promise that an alert is written once, and that
    # promise should not depend on the caller being careful.
    unique: dict[str, Condition] = {}
    for c in conditions:
        unique.setdefault(c.key, c)

    now_active = set(unique)
    for key in active:
        if not judgeable(key):
            now_active.add(key)

    # When each key last said something, so a key that has just fired stays
    # quiet even if its condition has genuinely gone false and true again.
    last_fired: dict[str, int] = {}
    for entry in log:
        if entry.at > last_fired.get(entry.key, -1):
            last_fired[entry.key] = entry.at

    def cooling(key: str) -> bool:
        previous = last_fired.get(key)
        return previous is not None and now - previous < cooldown_ms

    fired = [
        Alert(
            key=c.key,
            severity=c.severity,
            subject=c.subject,
            message=c.message,
            at=now,
        )
        for c in unique.values()
        if c.key not in active and not cooling(c.key)
    ]

    return Outcome(
        active=frozenset(now_active),
        log=[*fired, *log][:limit],
        fired=fired,
    )


def dedupe_log(log: Iterable[Alert], floor_ms: int = REFIRE_FLOOR_MS) -> list[Alert]:
    """Drop repeats of a key that land within `floor_ms` of the one before.

    Heals a log an earlier build wrote: a fix alone does not remove what the bug
    already recorded. Keeps the oldest of a run, and returns newest-first.
    """
    oldest_first = sorted(log, key=lambda a: a.at)
    kept: list[Alert] = []
    last_seen: dict[str, int] = {}
    for alert in oldest_first:
        previous = last_seen.get(alert.key)
        if previous is not None and alert.at - previous < floor_ms:
            continue
        last_seen[alert.key] = alert.at
        kept.append(alert)
    return sorted(kept, key=lambda a: a.at, reverse=True)

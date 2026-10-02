"""Trading only at certain hours of the day.

A rule that works in London often does nothing in Tokyo, and a five-minute rule
run across all twenty-four hours is really being tested on three or four
different markets averaged together. Restricting it to one is how you find out
which market the edge was in.

Real timezones, not fixed offsets. London is 08:00 local whether or not the
clocks have gone forward, which is 07:00 UTC for half the year and 08:00 for the
other half. A session written as fixed UTC hours is a session that silently
slides by an hour twice a year, right through the period a backtest is measuring,
and neither the result nor the chart would show it. `zoneinfo` is in the standard
library and the tz database is in the runtime image, so this costs nothing.

Everything is judged on the bar's *close* - the moment the decision is made -
which is the same instant every other part of the engine is anchored to.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from venues.calendar import NSE_CLOSE, NSE_OPEN

#: Monday is 0, as `datetime.weekday()` counts.
WEEKDAYS = frozenset({0, 1, 2, 3, 4})


@dataclass(frozen=True)
class Session:
    """A window of the local day, on certain weekdays."""

    name: str
    start: time
    end: time
    #: An IANA zone. The whole point of the class.
    tz: str
    #: Which days it runs, `datetime.weekday()` style. Empty means every day,
    #: which is what a crypto session is.
    days: frozenset[int] = WEEKDAYS

    def contains(self, at: datetime) -> bool:
        """Whether this instant falls inside the window."""
        try:
            local = at.astimezone(ZoneInfo(self.tz))
        except (ZoneInfoNotFoundError, ValueError):
            # A zone the machine does not know is better treated as always open
            # than as never open: the second silently produces a run with no
            # trades, which reads as a strategy that never fired.
            return True

        if self.start <= self.end:
            inside = self.start <= local.time() < self.end
            day = local.weekday()
        else:
            # Crosses midnight - Sydney's morning is the previous UTC evening.
            # A moment after midnight belongs to the day the session began on.
            after_midnight = local.time() < self.end
            inside = local.time() >= self.start or after_midnight
            day = (local - timedelta(days=1)).weekday() if after_midnight else local.weekday()

        return inside and (not self.days or day in self.days)

    def describe(self) -> str:
        days = "" if not self.days or self.days == WEEKDAYS else " (some days)"
        return f"{self.name} {self.start:%H:%M}-{self.end:%H:%M} {self.tz}{days}"


#: The sessions people mean by name.
#:
#: The hours are the conventional ones for each centre rather than any single
#: exchange's: this is about when a market is busy, which is what a short-horizon
#: rule is really keyed to. India's are NSE's actual hours, because that one is
#: an exchange rather than a centre.
PRESETS: dict[str, Session] = {
    "london": Session("London", time(8, 0), time(16, 30), "Europe/London"),
    "newyork": Session("New York", time(8, 0), time(17, 0), "America/New_York"),
    "tokyo": Session("Tokyo", time(9, 0), time(18, 0), "Asia/Tokyo"),
    "sydney": Session("Sydney", time(7, 0), time(16, 0), "Australia/Sydney"),
    "india": Session("India", NSE_OPEN, NSE_CLOSE, "Asia/Kolkata"),
}


def preset(name: str) -> Session | None:
    return PRESETS.get(name.strip().lower().replace(" ", ""))


def in_any(sessions: tuple[Session, ...], at: datetime) -> bool:
    """Whether any of these is open. No sessions means always open.

    Any rather than all, so picking London and New York means either - which is
    what choosing two sessions obviously means, and would otherwise only be true
    during their overlap.
    """
    if not sessions:
        return True
    return any(s.contains(at) for s in sessions)

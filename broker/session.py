"""When the market is actually open.

Pure date arithmetic with the holidays passed in, so it can be exercised
without a network or a clock. Everything here is in IST, because that is the
only timezone the exchange has an opinion about.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

#: India has no daylight saving, so a fixed offset is exact rather than a
#: simplification.
IST = timezone(timedelta(hours=5, minutes=30))

#: NSE's continuous session. Pre-open (09:00-09:15) is excluded: orders are
#: collected then but nothing trades, so prices do not move.
OPEN = time(9, 15)
CLOSE = time(15, 30)


def in_session(at: datetime, holidays: frozenset[date] = frozenset()) -> bool:
    """Whether the exchange is trading at this instant.

    Weekends and listed holidays are closed, as is anything outside 09:15 to
    15:30 IST. A naive datetime is read as IST rather than rejected, since the
    only naive times this sees come from a caller already working in it.
    """
    local = at.astimezone(IST) if at.tzinfo is not None else at.replace(tzinfo=IST)
    if local.weekday() >= 5:  # Saturday, Sunday
        return False
    if local.date() in holidays:
        return False
    return OPEN <= local.time() <= CLOSE


def session_bounds(day: date) -> tuple[datetime, datetime]:
    """The open and close instants for a given day, in IST."""
    return (
        datetime.combine(day, OPEN, tzinfo=IST),
        datetime.combine(day, CLOSE, tzinfo=IST),
    )

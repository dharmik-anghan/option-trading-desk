"""When a venue trades.

The NSE's session, and the one distinction a second venue forces: some
markets never close. Pure date arithmetic with the holidays passed in, so it
can be exercised without a network or a clock.

Worth stating plainly, because a lot of the desk's behaviour hangs off it. The
portfolio snapshot is skipped outside session hours, staleness warnings assume
prices should be moving, and "no change since yesterday" means nothing on a
market with no yesterday.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum

#: India has no daylight saving, so a fixed offset is exact rather than a
#: simplification.
IST = timezone(timedelta(hours=5, minutes=30))

#: NSE's continuous session. Pre-open (09:00-09:15) is excluded: orders are
#: collected then but nothing trades, so prices do not move. The close is also
#: when an expiring contract settles.
NSE_OPEN = time(9, 15)
NSE_CLOSE = time(15, 30)


def in_session(at: datetime, holidays: frozenset[date] = frozenset()) -> bool:
    """Whether the NSE is trading at this instant.

    Weekends and listed holidays are closed, as is anything outside 09:15 to
    15:30 IST. A naive datetime is read as IST rather than rejected, since the
    only naive times this sees come from a caller already working in it.
    """
    local = at.astimezone(IST) if at.tzinfo is not None else at.replace(tzinfo=IST)
    if local.weekday() >= 5:  # Saturday, Sunday
        return False
    if local.date() in holidays:
        return False
    return NSE_OPEN <= local.time() <= NSE_CLOSE


def session_bounds(day: date) -> tuple[datetime, datetime]:
    """The NSE's open and close instants for a given day, in IST."""
    return (
        datetime.combine(day, NSE_OPEN, tzinfo=IST),
        datetime.combine(day, NSE_CLOSE, tzinfo=IST),
    )


def next_open(at: datetime, holidays: frozenset[date] = frozenset()) -> datetime:
    """The next instant the NSE opens after `at`, or `at` itself if it is open.

    Steps a day at a time past weekends and holidays; a fortnight is more than
    any run of closed days the exchange has had.
    """
    local = at.astimezone(IST) if at.tzinfo is not None else at.replace(tzinfo=IST)
    if in_session(local, holidays):
        return local
    day = local.date()
    for _ in range(15):
        opens, _close = session_bounds(day)
        if day.weekday() < 5 and day not in holidays and opens > local:
            return opens
        day += timedelta(days=1)
    raise ValueError("no session in the next fortnight")


class Session(StrEnum):
    """Which calendar an instrument keeps.

    Note this belongs to an instrument, not to a venue: one crypto exchange
    lists BTCUSDT, which never closes, alongside gold and oil perpetuals, which
    track underlying futures that stand down at the weekend. A venue has a
    default; an instrument can differ from it.
    """

    NSE_FO = "nse_fo"
    #: Never closes. Every USDT perpetual on Shark, gold and oil included - the
    #: contract keeps trading through the weekend even where its underlying
    #: future does not. Checked against a live Saturday stream rather than
    #: assumed, because the reasonable assumption turned out to be wrong.
    ALWAYS = "always"


def is_open(session: Session, at: datetime, holidays: frozenset[date] = frozenset()) -> bool:
    """Whether an instrument on this calendar is trading at `at`.

    Holidays are ignored for a market that has none rather than being an error -
    callers should not have to know which calendars take a holiday list.

    """
    if session is Session.ALWAYS:
        return True
    return in_session(at, holidays)

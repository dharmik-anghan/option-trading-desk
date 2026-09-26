"""When a venue trades.

`broker/session.py` answers this for the NSE and is deliberately left alone -
it is exact, well tested, and knows about pre-open and holidays. This adds the
one distinction a second venue forces: some markets never close.

Worth stating plainly, because a lot of the desk's behaviour hangs off it. The
portfolio snapshot is skipped outside session hours, staleness warnings assume
prices should be moving, and "no change since yesterday" means nothing on a
market with no yesterday.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from broker.session import IST, in_session


class Session(StrEnum):
    """Which calendar an instrument keeps.

    Note this belongs to an instrument, not to a venue: one crypto exchange
    lists BTCUSDT, which never closes, alongside gold and oil perpetuals, which
    track underlying futures that stand down at the weekend. A venue has a
    default; an instrument can differ from it.
    """

    NSE_FO = "nse_fo"
    #: Never closes. Crypto pairs.
    ALWAYS = "always"
    #: Round the clock on weekdays, shut at the weekend. The tradfi perpetuals -
    #: gold, silver, oil, index and equity proxies - whose underlying futures do
    #: not trade Saturday or Sunday.
    WEEKDAYS_24H = "weekdays_24h"


def is_open(session: Session, at: datetime, holidays: frozenset[date] = frozenset()) -> bool:
    """Whether an instrument on this calendar is trading at `at`.

    Holidays are ignored for a market that has none rather than being an error -
    callers should not have to know which calendars take a holiday list.

    Weekends are judged in IST, which is where this desk is. A market keeping New
    York hours reopens on Sunday evening local time, which is Monday morning
    here; treating that as shut is a few hours of pessimism, and the honest fix
    is the venue publishing its own hours rather than us guessing at them.
    """
    if session is Session.ALWAYS:
        return True
    local = at.astimezone(IST) if at.tzinfo is not None else at.replace(tzinfo=IST)
    if session is Session.WEEKDAYS_24H:
        return local.weekday() < 5
    return in_session(at, holidays)

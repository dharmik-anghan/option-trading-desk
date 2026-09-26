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

from broker.session import in_session


class Session(StrEnum):
    NSE_FO = "nse_fo"
    #: 24/7, with no holidays and no close - crypto and the tradfi perpetuals
    #: quoted against it.
    ALWAYS = "always"


def is_open(session: Session, at: datetime, holidays: frozenset[date] = frozenset()) -> bool:
    """Whether a venue on this calendar is trading at `at`.

    Holidays are ignored for a market that has none, rather than being an error
    - callers should not have to know which venues take a holiday list.
    """
    if session is Session.ALWAYS:
        return True
    return in_session(at, holidays)

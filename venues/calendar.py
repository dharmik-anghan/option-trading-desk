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

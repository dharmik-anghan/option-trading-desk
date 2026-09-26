"""Walking a series backwards in time until it is deep enough to test on.

The read-through service in `service.py` keeps a chart current: it asks for the
most recent bars and stores what it gets, so history accumulates as the desk is
used. That is the right shape for a chart and the wrong one for a backtest, which
needs three years on the first day rather than in three years.

So this pages. It names a start time, takes a thousand bars, names the time after
the last one, and repeats. Two properties matter more than speed:

  Resumable. Every page is written before the next is asked for, so an interrupted
  run keeps what it fetched and the next run carries on from the end of it. Three
  hundred requests is long enough that "start again" is not an acceptable answer to
  a dropped connection.

  Idempotent. The store's primary key is the series and the timestamp, so a page
  that overlaps one already held replaces it rather than doubling it. Running this
  twice is a waste of time and nothing worse.

It is deliberately not part of the service. A backfill is something an operator
starts, watches, and may interrupt - not something a chart triggers by being
rendered.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from marketdata.models import Bar, Interval, Series
from marketdata.store import BarStore
from marketdata.yahoo import RateLimited

log = logging.getLogger(__name__)

#: Between pages. Binance allows 1,200 weight a minute and a full page costs 2, so
#: the budget is nowhere near the binding constraint - this is about not being a
#: nuisance on somebody else's free endpoint.
BETWEEN_PAGES = 0.25

#: After a refusal, before trying that page again. A rate limit is answered by
#: waiting; asking again immediately is how a 429 becomes Binance's 418.
AFTER_REFUSAL = 60.0

#: How many refusals in a row before giving up. Enough to ride out a short block,
#: few enough that a real one does not leave a script running all night.
MAX_REFUSALS = 5


class PagedSource(Protocol):
    """A source that can be asked for bars from a given moment."""

    def fetch_from(self, symbol: str, interval: Interval, start: datetime) -> list[Bar]: ...


@dataclass(frozen=True)
class Progress:
    """Where a backfill has got to, for something to print."""

    page: int
    written: int
    #: The newest bar fetched so far, which is what tells you how far there is left.
    through: datetime


def backfill(
    store: BarStore,
    source: PagedSource,
    series: Series,
    symbol: str,
    start: datetime,
    *,
    until: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Iterator[Progress]:
    """Fetch `series` from `start` to now, a page at a time.

    `symbol` is what the source calls the instrument, which need not be what the
    series calls it - the store keys on the desk's spelling and the source is asked
    in its own.

    Yields after each page rather than returning at the end, so a caller can print
    progress on a run that takes minutes. A caller that does not care can exhaust it
    with `deque(..., maxlen=0)`.
    """
    end = until or now()
    at = start
    page = 0
    written = 0
    refusals = 0

    while at < end:
        try:
            bars = source.fetch_from(symbol, series.interval, at)
        except RateLimited:
            refusals += 1
            if refusals >= MAX_REFUSALS:
                log.warning("giving up on %s after %d refusals", series.symbol, refusals)
                return
            log.info("rate limited, waiting %.0fs", AFTER_REFUSAL)
            sleep(AFTER_REFUSAL)
            continue
        refusals = 0

        # No bars means the source has nothing at or after this moment: either the
        # instrument did not exist yet or we have reached the present. Either way
        # there is no next page to ask for, because the only way to move forward is
        # from the last bar received.
        if not bars:
            return

        written += store.write(series, bars)
        page += 1
        newest = bars[-1].ts
        yield Progress(page=page, written=written, through=newest)

        # One bar past the last, so the next page does not repeat it. Guarding
        # against a source that returns a single bar it has already given us,
        # which would otherwise loop forever asking the same question.
        following = newest + timedelta(seconds=series.interval.seconds)
        if following <= at:
            return
        at = following
        sleep(BETWEEN_PAGES)


def resume_from(store: BarStore, series: Series, wanted_start: datetime) -> datetime:
    """Where a backfill should begin, given what is already held.

    Carries on from the newest stored bar when there is one, because the gap
    between then and now is the only part missing. When the store holds nothing, or
    holds bars that begin after the window asked for, it starts where asked - the
    older half is fetched and the overlap simply replaces itself.
    """
    span = store.span(series)
    if span is None:
        return wanted_start
    first, last = span
    if first > wanted_start:
        return wanted_start
    return last

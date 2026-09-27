"""A broker's own history, as a bar source.

The bars you trade from. A venue serves a short window - Shark gives a few hundred
candles - so its answers are stored as they arrive and the history accumulates from
the venue itself: a few hundred today, a few hundred overlapping tomorrow, and after
a month a month of the exact instrument the position is in.

That is the whole fix for a short history, and it needs no other source. Yahoo and
Binance are for context and for seeding what does not exist yet, and neither quotes
what Shark quotes - its gold is a perpetual where Yahoo's is a dated contract 0.8%
away, and its Bitcoin carries funding where Binance's is spot.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from broker.base import MarketData
from broker.errors import BrokerError
from broker.errors import RateLimited as BrokerRateLimited
from marketdata.models import Bar, Interval
from marketdata.yahoo import Fetched, RateLimited, Unavailable

log = logging.getLogger(__name__)

#: How the desk's intervals map onto the resolution strings a broker wants. The
#: options broker counts minutes; this is the same table the perps router uses, held
#: here so a source can be built without one.
RESOLUTION: dict[Interval, str] = {
    Interval.M1: "1",
    Interval.M5: "5",
    Interval.M15: "15",
    Interval.M30: "30",
    Interval.H1: "60",
    Interval.H4: "240",
    Interval.D1: "D",
    Interval.W1: "W",
}


class VenueBars:
    """Bars from a broker adapter, for storing under that venue's name."""

    def __init__(self, broker: MarketData) -> None:
        self._broker = broker

    def fetch(self, symbol: str, interval: Interval, days: int) -> Fetched:
        """Whatever the venue will give for the last `days`.

        A venue's own rate limit is the reason this is worth storing: Shark allows 60
        requests a minute across everything, and a chart redrawn on every timeframe
        change would spend that on candles already on disk.
        """
        today = datetime.now(UTC).date()
        start: date = today - timedelta(days=max(1, days))
        try:
            candles = self._broker.get_history(symbol, RESOLUTION[interval], start, today)
        except BrokerRateLimited as exc:
            raise RateLimited(str(exc.message)) from exc
        except BrokerError as exc:
            raise Unavailable(str(exc.message)) from exc

        bars = [
            Bar(
                ts=c.timestamp if c.timestamp.tzinfo else c.timestamp.replace(tzinfo=UTC),
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
            )
            for c in candles
        ]
        bars.sort(key=lambda b: b.ts)
        return Fetched(bars=bars, name=symbol, currency="")

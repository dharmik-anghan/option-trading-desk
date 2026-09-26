"""The latest price per symbol, and a fan-out to whoever is listening.

Two jobs, because they have different failure modes.

Holding the latest price is a dictionary, and the reason it exists at all is that
polling a venue for a price it already pushed is how a 60-request-a-minute budget
gets spent on nothing. The alert watcher reads from here rather than calling out.

Fanning out is queues, one per subscriber, each bounded. Bounded matters: a
browser that stops reading must not grow a queue until the process dies, and a
slow reader must not hold up the stream for everyone else. A full queue drops its
oldest tick, because for a price feed the newest value is the only one that
matters - this is not a log.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from broker.models import Tick

log = logging.getLogger(__name__)

#: Ticks a subscriber may fall behind before the oldest are dropped. A second or
#: two of updates: enough to ride out a slow render, far short of a leak.
QUEUE_LIMIT = 64


class TickHub:
    """Where live prices arrive, and where readers get them."""

    def __init__(self) -> None:
        self._latest: dict[str, Tick] = {}
        self._subscribers: set[asyncio.Queue[Tick]] = set()
        #: Counted so the desk can say whether the stream is actually delivering,
        #: rather than showing a price with no idea how old it is.
        self.received = 0
        self.dropped = 0
        self.last_at: datetime | None = None

    # ----------------------------------------------------------------- writing

    def publish(self, tick: Tick) -> None:
        """Record a tick and hand it to every subscriber.

        Called from the stream's callback, which runs on the event loop, so this
        is deliberately not a coroutine: making it one would mean the adapter
        needed to await a hub, and a synchronous put is exactly what a bounded
        queue offers.
        """
        self._latest[tick.symbol] = tick
        self.received += 1
        self.last_at = datetime.now(UTC)
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(tick)
            except asyncio.QueueFull:
                # Drop the oldest, keep the newest: a stale price is worse than a
                # missing one, and this is a feed rather than a log.
                try:
                    queue.get_nowait()
                    queue.put_nowait(tick)
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover - race
                    pass
                self.dropped += 1

    # ----------------------------------------------------------------- reading

    def price(self, symbol: str) -> float | None:
        """The last price seen, or None if the stream has never carried it.

        None rather than zero, so a caller cannot mistake "no stream yet" for a
        market at nothing - which for a level being watched would fire every
        downward alert at once.
        """
        tick = self._latest.get(symbol)
        return tick.price if tick is not None else None

    def prices(self) -> dict[str, float]:
        """Every price currently known, for a caller that wants them all."""
        return {symbol: tick.price for symbol, tick in self._latest.items()}

    def age_seconds(self, symbol: str) -> float | None:
        """How long since this symbol last moved, by our clock.

        Our clock, not the venue's: the question this answers is "is the stream
        alive", and comparing the venue's timestamp to ours would conflate a dead
        connection with a clock that disagrees.
        """
        tick = self._latest.get(symbol)
        if tick is None:
            return None
        return (datetime.now(UTC) - tick.at).total_seconds()

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[Tick]]:
        """A queue of ticks for one reader, removed when the reader goes away.

        A context manager because the removal is the important half: a browser
        that closes its tab leaves a queue nothing will ever read, and a set of
        those is a leak that only shows up after a long session.
        """
        queue: asyncio.Queue[Tick] = asyncio.Queue(maxsize=QUEUE_LIMIT)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    # There is deliberately no `async def stream()` generator here. It reads
    # better at the call site and leaks: when a consumer breaks out of `async
    # for`, the generator is suspended rather than closed, so the `finally` that
    # unregisters the queue does not run until garbage collection. A browser
    # disconnecting mid-iteration is exactly that case, which is the leak
    # `subscribe` exists to prevent. Callers take the context manager and own
    # their loop; it is two more lines and it cannot leak.

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

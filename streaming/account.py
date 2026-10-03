"""Account changes, fanned out to whoever is listening.

The tick hub's little sibling. An account event carries no figures - it says
the positions, orders or fills may have moved - so this holds only when the
last one came and a bounded queue per reader.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from broker.fyers.account_stream import AccountEvent

#: Events a reader may fall behind before the oldest are dropped. Each one only
#: says "read the account again", so a reader that missed several needs one.
QUEUE_LIMIT = 16


class AccountHub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[AccountEvent]] = set()
        self.last_at: datetime | None = None
        self.received = 0

    def publish(self, event: AccountEvent) -> None:
        self.last_at = event.at
        self.received += 1
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[AccountEvent]]:
        queue: asyncio.Queue[AccountEvent] = asyncio.Queue(maxsize=QUEUE_LIMIT)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

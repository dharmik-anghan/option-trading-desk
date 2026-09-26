"""The price hub: what it holds, and what it does when a reader falls behind.

The dropping behaviour is the part worth testing. A browser that stops reading
must not grow a queue until the process dies, and a slow reader must not hold up
the stream for everyone else - so a full queue loses its oldest tick rather than
blocking or growing.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from broker.models import Tick
from streaming.hub import QUEUE_LIMIT, TickHub


def tick(symbol: str = "BTCUSDT", price: float = 84000.0, ago: float = 0.0) -> Tick:
    return Tick(symbol=symbol, price=price, at=datetime.now(UTC) - timedelta(seconds=ago))


class TestHolding:
    def test_nothing_known_before_a_tick(self) -> None:
        hub = TickHub()
        assert hub.price("BTCUSDT") is None
        assert hub.prices() == {}

    def test_an_unseen_symbol_is_none_not_zero(self) -> None:
        # Zero would be a market at nothing, which for a level being watched
        # would fire every downward alert at once.
        hub = TickHub()
        hub.publish(tick("BTCUSDT"))
        assert hub.price("XAUUSDT") is None

    def test_the_latest_price_wins(self) -> None:
        hub = TickHub()
        hub.publish(tick(price=84000.0))
        hub.publish(tick(price=84100.0))
        assert hub.price("BTCUSDT") == 84100.0

    def test_symbols_are_kept_apart(self) -> None:
        hub = TickHub()
        hub.publish(tick("BTCUSDT", 84000.0))
        hub.publish(tick("XAUUSDT", 4287.0))
        assert hub.prices() == {"BTCUSDT": 84000.0, "XAUUSDT": 4287.0}

    def test_age_says_how_stale_a_price_is(self) -> None:
        hub = TickHub()
        hub.publish(tick(ago=5.0))
        age = hub.age_seconds("BTCUSDT")
        assert age is not None and 4.0 < age < 7.0

    def test_age_of_something_never_seen_is_none(self) -> None:
        assert TickHub().age_seconds("BTCUSDT") is None

    def test_it_counts_what_it_received(self) -> None:
        hub = TickHub()
        for _ in range(3):
            hub.publish(tick())
        assert hub.received == 3
        assert hub.last_at is not None


class TestFanOut:
    def test_a_subscriber_gets_ticks(self) -> None:
        async def run() -> None:
            hub = TickHub()
            with hub.subscribe() as queue:
                hub.publish(tick(price=1.0))
                hub.publish(tick(price=2.0))
                assert (await queue.get()).price == 1.0
                assert (await queue.get()).price == 2.0

        asyncio.run(run())

    def test_every_subscriber_gets_every_tick(self) -> None:
        async def run() -> None:
            hub = TickHub()
            with hub.subscribe() as first, hub.subscribe() as second:
                hub.publish(tick(price=7.0))
                assert (await first.get()).price == 7.0
                assert (await second.get()).price == 7.0

        asyncio.run(run())

    def test_a_subscriber_is_forgotten_when_it_leaves(self) -> None:
        # A browser that closes its tab leaves a queue nothing will read, and a
        # set of those is a leak that only shows after a long session.
        async def run() -> None:
            hub = TickHub()
            with hub.subscribe():
                assert hub.subscriber_count == 1
            assert hub.subscriber_count == 0

        asyncio.run(run())

    def test_a_subscriber_that_stops_reading_loses_the_oldest(self) -> None:
        async def run() -> None:
            hub = TickHub()
            with hub.subscribe() as queue:
                for i in range(QUEUE_LIMIT + 10):
                    hub.publish(tick(price=float(i)))
                assert queue.qsize() == QUEUE_LIMIT
                assert hub.dropped == 10
                # the newest survived, which is the one that matters
                newest = [await queue.get() for _ in range(QUEUE_LIMIT)][-1]
                assert newest.price == float(QUEUE_LIMIT + 9)

        asyncio.run(run())

    def test_a_stalled_subscriber_does_not_stop_the_others(self) -> None:
        async def run() -> None:
            hub = TickHub()
            with hub.subscribe() as stalled, hub.subscribe() as reading:
                for i in range(QUEUE_LIMIT + 5):
                    hub.publish(tick(price=float(i)))
                    if not reading.empty():
                        await reading.get()
                assert stalled.qsize() == QUEUE_LIMIT
                # publishing never raised, which is the promise
                assert hub.received == QUEUE_LIMIT + 5

        asyncio.run(run())

    def test_publishing_with_nobody_listening_is_fine(self) -> None:
        hub = TickHub()
        hub.publish(tick())
        assert hub.received == 1
        assert hub.subscriber_count == 0

    def test_a_reader_that_breaks_out_still_unregisters(self) -> None:
        # The reason there is no generator helper on the hub: breaking out of an
        # `async for` suspends a generator rather than closing it, so its cleanup
        # waits for garbage collection. With the context manager the queue is
        # gone the moment the reader leaves, which is what a disconnecting browser
        # needs.
        async def run() -> None:
            hub = TickHub()
            with hub.subscribe() as queue:
                hub.publish(tick(price=3.0))
                assert (await queue.get()).price == 3.0
            assert hub.subscriber_count == 0

        asyncio.run(run())


def test_bad_frames_never_reach_the_hub() -> None:
    # parse_tick is the gate; the hub trusts what it is given, so the gate is
    # tested where it lives - this just records the division of labour.
    from broker.shark.stream import parse_tick

    assert parse_tick({"s": "BTCUSDT", "c": "0"}) is None
    with pytest.raises(AttributeError):
        TickHub().publish(None)  # type: ignore[arg-type]

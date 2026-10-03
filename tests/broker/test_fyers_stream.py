"""Fyers' tick stream: parsing its frames, and crossing from its thread to the loop."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

from broker.fyers.stream import FyersStream, parse_tick
from broker.models import Tick
from venues.instruments import INDIA_VIX

NIFTY = "NSE:NIFTY50-INDEX"


class TestParseTick:
    def test_an_index_update_is_a_tick(self) -> None:
        tick = parse_tick(
            {
                "symbol": NIFTY,
                "ltp": 22421.95,
                "chp": 0.12,
                "exch_feed_time": 1759300000,
                "type": "if",
            }
        )
        assert tick is not None
        assert tick.symbol == NIFTY
        assert tick.price == 22421.95
        assert tick.change_pct == 0.12
        assert tick.at.timestamp() == 1759300000

    def test_housekeeping_is_not_a_tick(self) -> None:
        # Connection and subscription acknowledgements come down the same callback.
        assert (
            parse_tick({"type": "sub", "code": 11011, "message": "Subscribed", "s": "ok"}) is None
        )
        assert parse_tick("Connected") is None

    def test_a_zero_price_is_a_bad_frame(self) -> None:
        assert parse_tick({"symbol": NIFTY, "ltp": 0}) is None

    def test_a_missing_time_falls_back_to_now(self) -> None:
        tick = parse_tick({"symbol": NIFTY, "ltp": 100.0})
        assert tick is not None
        assert tick.change_pct is None


class FakeSocket:
    """Calls back from its own thread, as the library's socket does."""

    def __init__(
        self,
        login: str,
        on_open: Callable[[], None],
        on_message: Callable[[Any], None],
        on_error: Callable[[Any], None],
        on_close: Callable[[Any], None],
    ) -> None:
        self.login = login
        self.on_open = on_open
        self.on_message = on_message
        self.subscribed: list[str] = []
        self.history: list[tuple[str, list[str]]] = []
        self.closed = False
        self.alive = True

    def connect(self) -> None:
        self.on_open()

    def subscribe(self, symbols: list[str], data_type: str = "SymbolUpdate") -> None:
        self.subscribed = list(symbols)
        self.history.append(("sub", list(symbols)))

    def unsubscribe(self, symbols: list[str], data_type: str = "SymbolUpdate") -> None:
        self.history.append(("unsub", list(symbols)))

    def close_connection(self) -> None:
        self.closed = True
        self.alive = False

    def is_connected(self) -> bool:
        return self.alive

    def push_from_thread(self, message: dict[str, Any]) -> None:
        thread = threading.Thread(target=self.on_message, args=(message,))
        thread.start()
        thread.join()


class Harness:
    def __init__(self) -> None:
        self.sockets: list[FakeSocket] = []
        self.token = "t1"

    def factory(self, *args: Any) -> FakeSocket:
        socket = FakeSocket(*args)
        self.sockets.append(socket)
        return socket

    def login(self) -> tuple[str, str]:
        return "APP-100", self.token


def test_it_subscribes_the_listed_symbols_and_the_vix() -> None:
    h = Harness()

    async def run() -> None:
        stream = FyersStream(socket_factory=h.factory, token=h.login, check_every=60)
        await stream.start([NIFTY], lambda _: None)
        await stream.stop()

    asyncio.run(run())
    assert h.sockets[0].login == "APP-100:t1"
    assert h.sockets[0].subscribed == [NIFTY, INDIA_VIX]
    assert h.sockets[0].closed


def test_a_tick_from_the_socket_thread_arrives_on_the_loop() -> None:
    h = Harness()
    seen: list[tuple[Tick, bool]] = []

    async def run() -> None:
        loop_thread = threading.get_ident()

        def on_tick(tick: Tick) -> None:
            seen.append((tick, threading.get_ident() == loop_thread))

        stream = FyersStream(socket_factory=h.factory, token=h.login, check_every=60)
        await stream.start([NIFTY], on_tick)
        h.sockets[0].push_from_thread({"symbol": NIFTY, "ltp": 22400.5})
        await asyncio.sleep(0.05)
        assert stream.ticks_received == 1
        await stream.stop()

    asyncio.run(run())
    assert len(seen) == 1
    tick, on_loop = seen[0]
    assert tick.price == 22400.5
    assert on_loop, (
        "the hub belongs to the loop; a tick must not be published from the socket thread"
    )


def test_a_new_token_reopens_the_socket() -> None:
    # Tokens die at 06:00 IST; a socket holding yesterday's is a silent stream.
    h = Harness()

    async def run() -> None:
        stream = FyersStream(socket_factory=h.factory, token=h.login, check_every=0.01)
        await stream.start([NIFTY], lambda _: None)
        h.token = "t2"
        for _ in range(100):
            await asyncio.sleep(0.01)
            if len(h.sockets) > 1:
                break
        await stream.stop()

    asyncio.run(run())
    assert len(h.sockets) == 2
    assert h.sockets[0].closed
    assert h.sockets[1].login == "APP-100:t2"
    assert h.sockets[1].subscribed[0] == NIFTY


def test_a_dropped_connection_is_reopened_after_two_misses() -> None:
    h = Harness()

    async def run() -> None:
        stream = FyersStream(socket_factory=h.factory, token=h.login, check_every=0.01)
        await stream.start([NIFTY], lambda _: None)
        h.sockets[0].alive = False
        for _ in range(100):
            await asyncio.sleep(0.01)
            if len(h.sockets) > 1:
                break
        assert stream.reconnects == 1
        await stream.stop()

    asyncio.run(run())
    assert len(h.sockets) == 2


class DeadSocket(FakeSocket):
    """A socket whose connect fails, though the library still calls on_open."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.alive = False

    def connect(self) -> None:
        self.on_open()


def test_a_failed_open_does_not_subscribe() -> None:
    # Subscribing calls Fyers' REST API; a socket that never opened must not
    # spend the rate limit on it every few seconds.
    sockets: list[DeadSocket] = []

    def factory(*args: Any) -> DeadSocket:
        sockets.append(DeadSocket(*args))
        return sockets[-1]

    async def run() -> None:
        stream = FyersStream(
            socket_factory=factory, token=lambda: ("APP-100", "t1"), check_every=60
        )
        await stream.start([NIFTY], lambda _: None)
        assert not stream.connected
        await stream.stop()

    asyncio.run(run())
    assert sockets[0].subscribed == []


def test_a_socket_that_keeps_failing_is_retried_less_and_less() -> None:
    sockets: list[DeadSocket] = []

    def factory(*args: Any) -> DeadSocket:
        sockets.append(DeadSocket(*args))
        return sockets[-1]

    async def run() -> None:
        stream = FyersStream(
            socket_factory=factory, token=lambda: ("APP-100", "t1"), check_every=0.01
        )
        await stream.start([NIFTY], lambda _: None)
        await asyncio.sleep(0.6)
        await stream.stop()

    asyncio.run(run())
    # Without backoff a reopen every two checks is ~30 sockets in 0.6s; with it,
    # waits of 0.02, 0.04, 0.08, 0.16, 0.32 allow only a handful.
    assert 2 <= len(sockets) <= 8, len(sockets)


LEG = "NSE:NIFTY26OCT23000CE"
OTHER = "NSE:NIFTY26OCT22000PE"


def test_a_watched_contract_is_subscribed_once_and_dropped_by_the_last_reader() -> None:
    h = Harness()

    async def run() -> None:
        stream = FyersStream(socket_factory=h.factory, token=h.login, check_every=60)
        await stream.start([NIFTY], lambda _: None)
        socket = h.sockets[0]
        socket.history.clear()

        await stream.watch([LEG])
        await stream.watch([LEG, OTHER])  # a second tab: only OTHER is new
        assert socket.history == [("sub", [LEG]), ("sub", [OTHER])]

        await stream.unwatch([LEG, OTHER])  # the second tab leaves
        assert socket.history[-1] == ("unsub", [OTHER])
        await stream.unwatch([LEG])  # the first one
        assert socket.history[-1] == ("unsub", [LEG])
        await stream.stop()

    asyncio.run(run())


def test_the_listed_indices_are_never_unsubscribed_by_a_reader() -> None:
    h = Harness()

    async def run() -> None:
        stream = FyersStream(socket_factory=h.factory, token=h.login, check_every=60)
        await stream.start([NIFTY], lambda _: None)
        socket = h.sockets[0]
        socket.history.clear()
        async with stream.watching([NIFTY, LEG]):
            pass
        assert socket.history == [("sub", [LEG]), ("unsub", [LEG])]
        await stream.stop()

    asyncio.run(run())


def test_a_reopened_socket_carries_the_watched_contracts_too() -> None:
    h = Harness()

    async def run() -> None:
        stream = FyersStream(socket_factory=h.factory, token=h.login, check_every=0.01)
        await stream.start([NIFTY], lambda _: None)
        await stream.watch([LEG])
        h.token = "t2"
        for _ in range(100):
            await asyncio.sleep(0.01)
            if len(h.sockets) > 1:
                break
        await stream.stop()

    asyncio.run(run())
    assert h.sockets[1].subscribed == [NIFTY, INDIA_VIX, LEG]

"""The order socket, used as a signal that the account changed."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

from broker.fyers.account_stream import SUBSCRIBED, AccountEvent, FyersAccountStream


class FakeOrderSocket:
    def __init__(
        self,
        login: str,
        on_open: Callable[[], None],
        on_event: Callable[[str, Any], None],
        on_error: Callable[[Any], None],
        on_close: Callable[[Any], None],
    ) -> None:
        self.on_open = on_open
        self.on_event = on_event
        self.alive = True
        self.subscribed: list[str] = []

    def connect(self) -> None:
        self.on_open()

    def subscribe(self, data_type: str) -> None:
        self.subscribed.append(data_type)

    def close_connection(self) -> None:
        self.alive = False

    def is_connected(self) -> bool:
        return self.alive


def test_a_fill_on_the_socket_thread_arrives_on_the_loop_as_an_event() -> None:
    sockets: list[FakeOrderSocket] = []
    seen: list[tuple[AccountEvent, bool]] = []

    def factory(*args: Any) -> FakeOrderSocket:
        sockets.append(FakeOrderSocket(*args))
        return sockets[-1]

    async def run() -> None:
        loop_thread = threading.get_ident()
        stream = FyersAccountStream(
            socket_factory=factory, token=lambda: ("APP", "t"), check_every=60
        )
        await stream.start(lambda e: seen.append((e, threading.get_ident() == loop_thread)))
        assert sockets[0].subscribed == [SUBSCRIBED]
        thread = threading.Thread(target=sockets[0].on_event, args=("trades", {"trades": {}}))
        thread.start()
        thread.join()
        await asyncio.sleep(0.05)
        await stream.stop()

    asyncio.run(run())
    assert [(e.kind, on_loop) for e, on_loop in seen] == [("trades", True)]


def test_a_socket_that_did_not_open_is_not_subscribed() -> None:
    sockets: list[FakeOrderSocket] = []

    def factory(*args: Any) -> FakeOrderSocket:
        socket = FakeOrderSocket(*args)
        socket.alive = False
        sockets.append(socket)
        return socket

    async def run() -> None:
        stream = FyersAccountStream(
            socket_factory=factory, token=lambda: ("APP", "t"), check_every=60
        )
        await stream.start(lambda e: None)
        assert not stream.connected
        await stream.stop()

    asyncio.run(run())
    assert sockets[0].subscribed == []

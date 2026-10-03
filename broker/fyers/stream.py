"""Fyers' live tick stream.

Fyers' data socket (`fyers_apiv3.FyersWebsocket.data_ws`), wrapped as the async
start/stop pair the app runs every venue's stream through. Keeping the socket
open - its thread, its daily token, reopening it - is `broker/fyers/socket.py`;
this is what the data socket carries: the indices always, and the contracts a
page asks for while it reads them.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any, Protocol

from broker.fyers.socket import CHECK_EVERY, Supervised, alive, prepare_sdk
from broker.models import Tick
from paths import LOGS_DIR
from venues.instruments import INDIA_VIX

log = logging.getLogger(__name__)

#: Symbols streamed whatever the desk lists: the VIX sits beside the indices on
#: the watchlist but is not an underlying anything is traded on.
ALSO_STREAMED = (INDIA_VIX,)


class DataSocket(Protocol):
    """The part of `FyersDataSocket` used here, so a test can stand in for it."""

    def connect(self) -> None: ...
    def subscribe(self, symbols: list[str], data_type: str = ...) -> None: ...
    def unsubscribe(self, symbols: list[str], data_type: str = ...) -> None: ...
    def close_connection(self) -> None: ...
    def is_connected(self) -> bool: ...


#: Builds a socket: (client_id:token, on_open, on_message, on_error, on_close).
SocketFactory = Callable[
    [str, Callable[[], None], Callable[[Any], None], Callable[[Any], None], Callable[[Any], None]],
    DataSocket,
]


def _fyers_socket(
    login: str,
    on_open: Callable[[], None],
    on_message: Callable[[Any], None],
    on_error: Callable[[Any], None],
    on_close: Callable[[Any], None],
) -> DataSocket:
    from fyers_apiv3.FyersWebsocket import data_ws

    prepare_sdk()
    LOGS_DIR.mkdir(exist_ok=True)
    socket: DataSocket = data_ws.FyersDataSocket(
        access_token=login,
        log_path=f"{LOGS_DIR}/",
        litemode=False,
        write_to_file=False,
        reconnect=True,
        on_connect=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    # The library makes its socket thread a daemon only when it is also writing
    # every frame to a log file. Without this the thread outlives shutdown and a
    # reload waits on it.
    socket.background_flag = True  # type: ignore[attr-defined]
    return socket


def parse_tick(message: Any) -> Tick | None:
    """One symbol update, or None if this message is not a usable price.

    The socket sends its own housekeeping down the same callback - connection
    and subscription acknowledgements with a `type` and a `code` - and those have
    no `ltp`. None rather than an exception: this runs on the socket's thread,
    and one odd frame should cost one frame, not the connection.
    """
    if not isinstance(message, dict):
        return None
    symbol = message.get("symbol")
    raw = message.get("ltp")
    if not symbol or raw is None:
        return None
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    feed = message.get("exch_feed_time")
    at = (
        datetime.fromtimestamp(int(feed), tz=UTC)
        if isinstance(feed, (int, float)) and feed > 0
        else datetime.now(UTC)
    )
    change = message.get("chp")
    try:
        change_pct = float(change) if change is not None else None
    except (TypeError, ValueError):
        change_pct = None
    return Tick(symbol=str(symbol), price=price, at=at, change_pct=change_pct)


class FyersStream(Supervised):
    """Index and contract prices pushed by Fyers, delivered on the event loop."""

    name = "fyers stream"

    def __init__(
        self,
        socket_factory: SocketFactory = _fyers_socket,
        token: Callable[[], tuple[str, str]] | None = None,
        check_every: float = CHECK_EVERY,
    ) -> None:
        super().__init__(token, check_every)
        self._make = socket_factory
        self._symbols: list[str] = []
        self._on_tick: Callable[[Tick], None] | None = None
        #: Contracts a page has asked for on top of the indices, counted per
        #: page: two tabs watching one leg must not have the first to close
        #: take it off the socket under the second.
        self._watched: Counter[str] = Counter()
        #: Counted rather than logged per frame: several ticks a second per
        #: index would bury everything else in the log.
        self.ticks_received = 0

    async def start(self, symbols: list[str], on_tick: Callable[[Tick], None]) -> None:
        """Connect and subscribe. Returns once the socket is open."""
        self._symbols = list(dict.fromkeys([*symbols, *ALSO_STREAMED]))
        self._on_tick = on_tick
        await self._begin()

    def _data_socket(self) -> DataSocket | None:
        socket = self._socket
        if socket is None or not self.connected or not alive(socket):
            return None
        return socket  # type: ignore[return-value]

    # ------------------------------------------------------------- watching

    @property
    def subscribed(self) -> list[str]:
        """Everything the socket should carry: the listed symbols, then the watched."""
        return list(dict.fromkeys([*self._symbols, *self._watched]))

    async def watch(self, symbols: Iterable[str]) -> None:
        """Stream these too, until as many `unwatch` calls have taken them off."""
        wanted = list(dict.fromkeys(symbols))
        new = [s for s in wanted if s not in self._watched and s not in self._symbols]
        self._watched.update(wanted)
        socket = self._data_socket()
        if new and socket is not None:
            # Subscribing calls a REST endpoint to turn symbols into tokens.
            await asyncio.to_thread(socket.subscribe, symbols=new, data_type="SymbolUpdate")

    async def unwatch(self, symbols: Iterable[str]) -> None:
        gone: list[str] = []
        for symbol in dict.fromkeys(symbols):
            if self._watched[symbol] <= 1:
                self._watched.pop(symbol, None)
                if symbol not in self._symbols:
                    gone.append(symbol)
            else:
                self._watched[symbol] -= 1
        socket = self._data_socket()
        if gone and socket is not None:
            with suppress(Exception):
                await asyncio.to_thread(socket.unsubscribe, symbols=gone, data_type="SymbolUpdate")

    @asynccontextmanager
    async def watching(self, symbols: Iterable[str]) -> AsyncIterator[None]:
        """`watch` for the life of a block - a browser's stream, say."""
        wanted = list(symbols)
        await self.watch(wanted)
        try:
            yield
        finally:
            await self.unwatch(wanted)

    # ------------------------------------------------- callbacks, on its thread

    def _build(self, login: str) -> DataSocket:
        return self._make(login, self._opened, self._message, self._error, self._closed)

    def _opened(self) -> None:
        """Subscribe on every open, which is when a fresh socket has nothing.

        Subscribing calls a Fyers REST endpoint, so it is only done for a socket
        that actually opened.
        """
        socket = self._really_open()
        if socket is None:
            return
        symbols = self.subscribed
        socket.subscribe(symbols=symbols, data_type="SymbolUpdate")  # type: ignore[attr-defined]
        log.info("fyers stream subscribed to %d symbols", len(symbols))

    def _message(self, message: Any) -> None:
        tick = parse_tick(message)
        if tick is not None:
            self._on_loop(self._deliver, tick)

    def _deliver(self, tick: Tick) -> None:
        """On the loop, where the hub lives."""
        self.ticks_received += 1
        self.connected = True
        if self._on_tick is not None:
            self._on_tick(tick)

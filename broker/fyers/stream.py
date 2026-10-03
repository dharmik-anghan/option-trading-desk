"""Fyers' live tick stream.

Fyers' own data socket (`fyers_apiv3.FyersWebsocket.data_ws`), wrapped as the
async start/stop pair the app runs every venue's stream through.

What makes this one different from the perpetuals stream:

- **It runs on its own thread.** The library drives a `websocket-client`
  connection on a thread and calls back from it. The hub is not thread-safe and
  belongs to the event loop, so every tick is handed across with
  `call_soon_threadsafe` rather than published from the callback.
- **Its login expires every morning.** A Fyers token dies at 06:00 IST. The REST
  adapter is rebuilt per request and picks up a refreshed token for free; a socket
  opened yesterday is still holding the old one. A watchdog checks the token and
  the connection every half minute and reopens the socket when either has gone.
- **Setting it up blocks.** Connecting sleeps and subscribing calls a REST
  endpoint to turn symbols into tokens, so both run off the loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Protocol

import certifi

from broker.fyers.token_store import get_access_token
from broker.models import Tick
from paths import LOGS_DIR
from settings import load_settings
from venues.instruments import INDIA_VIX

log = logging.getLogger(__name__)

#: How often the watchdog looks at the token and the connection.
CHECK_EVERY = 30.0

#: Longest the watchdog waits between attempts to reopen a socket that keeps
#: failing. Each attempt costs Fyers REST calls, so a socket that cannot connect
#: must not spend the rate limit the rest of the desk polls with.
MAX_BACKOFF = 900.0

#: Symbols streamed whatever the desk lists: the VIX sits beside the indices on
#: the watchlist but is not an underlying anything is traded on.
ALSO_STREAMED = (INDIA_VIX,)


class DataSocket(Protocol):
    """The part of `FyersDataSocket` used here, so a test can stand in for it."""

    def connect(self) -> None: ...
    def subscribe(self, symbols: list[str], data_type: str = ...) -> None: ...
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

    # websocket-client verifies against the platform's store unless told
    # otherwise, and on a python.org build of Python that store is empty: every
    # connect fails the certificate check and the library retries every few
    # seconds. The same bundle the perpetuals stream uses.
    os.environ.setdefault("WEBSOCKET_CLIENT_CA_BUNDLE", certifi.where())
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


class FyersStream:
    """Index prices pushed by Fyers, delivered on the event loop."""

    def __init__(
        self,
        socket_factory: SocketFactory = _fyers_socket,
        token: Callable[[], tuple[str, str]] | None = None,
        check_every: float = CHECK_EVERY,
    ) -> None:
        self._make = socket_factory
        self._token = token or _login
        self._check_every = check_every
        self._symbols: list[str] = []
        self._on_tick: Callable[[Tick], None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._socket: DataSocket | None = None
        self._login: str | None = None
        self._watch: asyncio.Task[None] | None = None
        #: Counted rather than logged per frame: several ticks a second per
        #: index would bury everything else in the log.
        self.ticks_received = 0
        self.connected = False
        self.reconnects = 0

    async def start(self, symbols: list[str], on_tick: Callable[[Tick], None]) -> None:
        """Connect and subscribe. Returns once the socket is open."""
        self._loop = asyncio.get_running_loop()
        self._symbols = list(dict.fromkeys([*symbols, *ALSO_STREAMED]))
        self._on_tick = on_tick
        await self._open()
        self._watch = asyncio.create_task(self._watchdog(), name="fyers-stream-watchdog")

    async def stop(self) -> None:
        """Disconnect. Safe to call when not connected."""
        if self._watch is not None:
            self._watch.cancel()
            with suppress(asyncio.CancelledError):
                await self._watch
            self._watch = None
        await self._close()

    # ------------------------------------------------------------ connection

    async def _open(self) -> None:
        login = await asyncio.to_thread(self._token)
        joined = f"{login[0]}:{login[1]}"
        socket = self._make(joined, self._opened, self._message, self._error, self._closed)
        self._socket = socket
        self._login = joined
        await asyncio.to_thread(socket.connect)

    async def _close(self) -> None:
        socket, self._socket = self._socket, None
        self.connected = False
        if socket is not None:
            with suppress(Exception):
                await asyncio.to_thread(socket.close_connection)

    async def _watchdog(self) -> None:
        """Reopen the socket when the token has changed or the connection has gone.

        Two misses in a row before reopening a dropped connection: the library
        reconnects by itself, and a check that lands mid-reconnect should not
        tear down a socket that was about to recover. A reopen that does not
        take doubles the wait before the next, up to `MAX_BACKOFF`.
        """
        missed = 0
        failures = 0
        not_before = 0.0
        while True:
            await asyncio.sleep(self._check_every)
            try:
                login = await asyncio.to_thread(self._token)
            except Exception:  # noqa: BLE001 - try again next round
                log.warning("fyers stream could not read a token", exc_info=True)
                continue
            fresh = f"{login[0]}:{login[1]}" != self._login
            socket = self._socket
            alive = socket is not None and _alive(socket)
            if alive:
                missed = failures = 0
                if not fresh:
                    continue
            else:
                missed += 1
                if not fresh and (missed < 2 or time.monotonic() < not_before):
                    continue
            log.info("fyers stream reopening (%s)", "new token" if fresh else "connection lost")
            await self._close()
            try:
                await self._open()
                self.reconnects += 1
            except Exception:  # noqa: BLE001 - the next round tries again
                log.warning("fyers stream could not reopen", exc_info=True)
            missed = 0
            if not fresh:
                failures += 1
                wait = min(MAX_BACKOFF, self._check_every * 2**failures)
                not_before = time.monotonic() + wait

    # ------------------------------------------------- callbacks, on its thread

    def _opened(self) -> None:
        """Subscribe on every open, which is when a fresh socket has nothing.

        The library calls this after every connect attempt, including one that
        failed. Subscribing calls a Fyers REST endpoint, so it is only done for
        a socket that actually opened.
        """
        socket = self._socket
        if socket is None or not _alive(socket):
            log.warning("fyers stream did not open")
            return
        self.connected = True
        socket.subscribe(symbols=self._symbols, data_type="SymbolUpdate")
        log.info("fyers stream subscribed to %s", ", ".join(self._symbols))

    def _message(self, message: Any) -> None:
        tick = parse_tick(message)
        loop = self._loop
        if tick is None or loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._deliver, tick)

    def _error(self, message: Any) -> None:
        log.warning("fyers stream error: %s", message)

    def _closed(self, message: Any) -> None:
        self.connected = False
        log.warning("fyers stream closed: %s", message)

    def _deliver(self, tick: Tick) -> None:
        """On the loop, where the hub lives."""
        self.ticks_received += 1
        self.connected = True
        if self._on_tick is not None:
            self._on_tick(tick)


def _alive(socket: DataSocket) -> bool:
    try:
        return bool(socket.is_connected())
    except Exception:  # noqa: BLE001 - a socket that cannot say is not alive
        return False


def _login() -> tuple[str, str]:
    """(client id, access token), refreshing the token if it has expired."""
    settings = load_settings()
    return settings.fyers_client_id, get_access_token(settings)

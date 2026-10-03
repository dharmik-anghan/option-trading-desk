"""What every Fyers socket here needs, whichever feed it carries.

Fyers' SDK sockets - the data socket and the order socket - share their
awkward parts, so they are handled once:

- **They run on their own thread** and call back from it. Anything bound for
  the app is handed to the event loop with `call_soon_threadsafe`.
- **Their login expires every morning.** A token dies at 06:00 IST and a socket
  opened yesterday still holds it. A watchdog checks the token and the
  connection every half minute and reopens on a new token or a lost
  connection, backing off while a reopen keeps failing.
- **Connecting blocks** (the SDK sleeps), so it runs off the loop.
- **The SDK calls "connected" after a failed connect too**, so whether a socket
  really opened is asked of the socket, not taken from the callback.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any, Protocol

import certifi

from broker.fyers.token_store import get_access_token
from settings import load_settings

log = logging.getLogger(__name__)

#: How often the watchdog looks at the token and the connection.
CHECK_EVERY = 30.0

#: Longest the watchdog waits between attempts to reopen a socket that keeps
#: failing. Each attempt can cost Fyers REST calls, so a socket that cannot
#: connect must not spend the rate limit the rest of the desk polls with.
MAX_BACKOFF = 900.0


class SdkSocket(Protocol):
    """The part of an SDK socket the supervisor uses."""

    def connect(self) -> None: ...
    def close_connection(self) -> None: ...
    def is_connected(self) -> bool: ...


def prepare_sdk() -> None:
    """Point websocket-client at a CA bundle before an SDK socket is built.

    It verifies against the platform's store unless told otherwise, and on a
    python.org build of Python that store is empty: every connect fails the
    certificate check and the SDK retries every few seconds. The same bundle
    the perpetuals stream uses.
    """
    os.environ.setdefault("WEBSOCKET_CLIENT_CA_BUNDLE", certifi.where())


def alive(socket: SdkSocket) -> bool:
    try:
        return bool(socket.is_connected())
    except Exception:  # noqa: BLE001 - a socket that cannot say is not alive
        return False


def fyers_login() -> tuple[str, str]:
    """(client id, access token), refreshing the token if it has expired."""
    settings = load_settings()
    return settings.fyers_client_id, get_access_token(settings)


class Supervised:
    """One SDK socket kept open: opened, watched, reopened, closed.

    A subclass says how to build its socket (`_build`) and what to do once it
    is open (`_opened`, on the socket's thread).
    """

    #: For the log.
    name = "fyers socket"

    def __init__(
        self,
        token: Callable[[], tuple[str, str]] | None = None,
        check_every: float = CHECK_EVERY,
    ) -> None:
        self._token = token or fyers_login
        self._check_every = check_every
        self._loop: asyncio.AbstractEventLoop | None = None
        self._socket: SdkSocket | None = None
        self._login: str | None = None
        self._watch: asyncio.Task[None] | None = None
        self.connected = False
        self.reconnects = 0

    def _build(self, login: str) -> SdkSocket:
        raise NotImplementedError

    def _opened(self) -> None:
        """Called by the SDK after a connect attempt, on its thread."""

    async def _begin(self) -> None:
        """Open the socket and start watching it."""
        self._loop = asyncio.get_running_loop()
        await self._open()
        self._watch = asyncio.create_task(self._watchdog(), name=f"{self.name} watchdog")

    async def stop(self) -> None:
        """Disconnect. Safe to call when not connected."""
        if self._watch is not None:
            self._watch.cancel()
            with suppress(asyncio.CancelledError):
                await self._watch
            self._watch = None
        await self._close()

    def _on_loop(self, fn: Callable[..., None], *args: Any) -> None:
        """Run `fn` on the event loop, from the socket's thread."""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(fn, *args)

    def _really_open(self) -> SdkSocket | None:
        """The socket, if it actually opened; logged when it did not."""
        socket = self._socket
        if socket is None or not alive(socket):
            log.warning("%s did not open", self.name)
            return None
        self.connected = True
        return socket

    def _error(self, message: Any) -> None:
        log.warning("%s error: %s", self.name, message)

    def _closed(self, message: Any) -> None:
        self.connected = False
        log.warning("%s closed: %s", self.name, message)

    async def _open(self) -> None:
        login = await asyncio.to_thread(self._token)
        joined = f"{login[0]}:{login[1]}"
        socket = self._build(joined)
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

        Two misses in a row before reopening a dropped connection: the SDK
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
                log.warning("%s could not read a token", self.name, exc_info=True)
                continue
            fresh = f"{login[0]}:{login[1]}" != self._login
            socket = self._socket
            if socket is not None and alive(socket):
                missed = failures = 0
                if not fresh:
                    continue
            else:
                missed += 1
                if not fresh and (missed < 2 or time.monotonic() < not_before):
                    continue
            log.info("%s reopening (%s)", self.name, "new token" if fresh else "connection lost")
            await self._close()
            try:
                await self._open()
                self.reconnects += 1
            except Exception:  # noqa: BLE001 - the next round tries again
                log.warning("%s could not reopen", self.name, exc_info=True)
            missed = 0
            if not fresh:
                failures += 1
                not_before = time.monotonic() + min(MAX_BACKOFF, self._check_every * 2**failures)

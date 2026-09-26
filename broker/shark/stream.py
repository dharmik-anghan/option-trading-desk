"""Shark's live tick stream.

socket.io, not a raw WebSocket. That distinction decides the dependency: the
protocol is a framing and handshake layer on top of WebSocket, so a WebSocket
client cannot speak it and `python-socketio` is doing real work rather than
wrapping something the standard library already has.

The stream is public - no key, no signature, no listen key. Only the private
streams (order and position updates) need one, and those are not used here yet.

What was learned getting this to connect, since none of it is in the docs:

- the endpoint is a different host from the REST API;
- subscription is an emitted `subscribe` event carrying lowercased topics, not a
  URL path or a query parameter;
- updates arrive as a `24hrTicker` event whose payload is the same single-letter
  shape the REST ticker uses, so `c` is the price;
- the certificate needs an explicit CA bundle. aiohttp's default SSL context
  could not verify this host, while curl could, so `certifi` is passed in rather
  than left to the platform.
"""

from __future__ import annotations

import logging
import ssl
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import aiohttp
import certifi
import socketio

from broker.models import Tick

log = logging.getLogger(__name__)

STREAM_URL = "https://fawss.sharkexchange.in"

#: The venue's event name for a ticker update.
TICKER_EVENT = "24hrTicker"


def topic(symbol: str) -> str:
    """"BTCUSDT" -> "btcusdt@ticker". Lowercase, or nothing arrives."""
    return f"{symbol.lower()}@ticker"


def parse_tick(payload: dict[str, Any]) -> Tick | None:
    """One ticker event, or None if it is not a usable price.

    None rather than an exception: this runs inside a callback on the stream's own
    task, and one malformed frame should cost one frame, not the connection.
    """
    symbol = payload.get("s")
    raw = payload.get("c")
    if not symbol or raw is None:
        return None
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        # A zero price is not a market, it is a bad frame.
        return None
    event_ms = payload.get("E")
    at = (
        datetime.fromtimestamp(int(event_ms) / 1000, tz=UTC)
        if isinstance(event_ms, (int, float))
        else datetime.now(UTC)
    )
    change = payload.get("P")
    try:
        change_pct = float(change) if change is not None else None
    except (TypeError, ValueError):
        change_pct = None
    return Tick(symbol=str(symbol), price=price, at=at, change_pct=change_pct)


class SharkStream:
    """The public ticker stream, as an async start/stop pair.

    Reconnection is the library's, which resubscribes through the `connect`
    handler - so a dropped connection recovers its topics without the caller
    knowing it happened. That matters more here than on the options desk: this
    venue runs overnight, when nobody is watching to notice a silent stream.
    """

    def __init__(self, url: str = STREAM_URL) -> None:
        self._url = url
        self._symbols: list[str] = []
        self._on_tick: Callable[[Tick], None] | None = None
        self._sio: socketio.AsyncClient | None = None
        self._session: aiohttp.ClientSession | None = None
        #: Counted rather than logged per frame: a tick a second per symbol would
        #: bury everything else in the log.
        self.ticks_received = 0
        self.connected = False

    async def start(self, symbols: list[str], on_tick: Callable[[Tick], None]) -> None:
        """Connect and subscribe. Returns once the stream is running."""
        self._symbols = list(symbols)
        self._on_tick = on_tick

        context = ssl.create_default_context(cafile=certifi.where())
        self._session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=context))
        sio = socketio.AsyncClient(
            http_session=self._session,
            reconnection=True,
            reconnection_delay=2,
        )
        self._sio = sio

        # Handlers registered by call rather than by decorator: socketio ships no
        # type information for its decorators, so mypy sees them as untyped and
        # every handler under one becomes untyped too.
        sio.on("connect", self._on_connect)
        sio.on("disconnect", self._on_disconnect)
        sio.on(TICKER_EVENT, self._on_ticker)

        await sio.connect(self._url, transports=["websocket"])

    async def _on_connect(self) -> None:
        """Subscribe on every connect, not only the first.

        The library reconnects by itself, and a reconnect starts a fresh session
        with no subscriptions - so resubscribing here is what makes recovery
        silent. Doing it once after connect() would leave a reconnected stream
        connected and mute, which is the worst failure available: it looks fine.
        """
        assert self._sio is not None
        self.connected = True
        topics = [topic(s) for s in self._symbols]
        await self._sio.emit("subscribe", {"params": topics, "id": 1})
        log.info("shark stream subscribed to %s", ", ".join(topics))

    async def _on_disconnect(self) -> None:
        self.connected = False
        log.warning("shark stream disconnected")

    async def _on_ticker(self, payload: dict[str, Any]) -> None:
        tick = parse_tick(payload)
        if tick is None or self._on_tick is None:
            return
        self.ticks_received += 1
        self._on_tick(tick)

    async def stop(self) -> None:
        """Disconnect. Safe to call when not connected."""
        if self._sio is not None:
            await self._sio.disconnect()
            self._sio = None
        if self._session is not None:
            await self._session.close()
            self._session = None
        self.connected = False

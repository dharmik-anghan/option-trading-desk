"""Ticks from the hub to a browser, as server-sent events.

Shared by every desk that streams. The hub is one per process and carries every
venue's ticks, so each route says which symbols it wants: the perpetuals page
has no use for NIFTY, and the options desk none for Bitcoin.

Server-sent events, not a websocket. The traffic is one-way - the desk needs
prices pushed and has nothing to say back - and SSE is a plain HTTP response a
browser reconnects by itself, where a websocket would mean a protocol upgrade,
a ping loop and reconnection logic of our own for the same result.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Collection
from typing import Protocol

from fastapi.responses import StreamingResponse

from broker.models import Tick
from streaming.hub import TickHub

#: How long to wait for a tick before sending a keep-alive. Proxies and browsers
#: drop a connection that has said nothing, and a silent instrument is normal at
#: three in the morning.
STREAM_HEARTBEAT = 15.0


class _Request(Protocol):
    async def is_disconnected(self) -> bool: ...


async def next_tick(queue: asyncio.Queue[Tick], closing: asyncio.Event | None) -> Tick | None:
    """The next tick, or None if the app is going down first.

    Raises TimeoutError when neither happens within the heartbeat, which is the
    normal case for an instrument nobody is trading at three in the morning.
    """
    if closing is None:
        return await asyncio.wait_for(queue.get(), timeout=STREAM_HEARTBEAT)

    waits = [asyncio.create_task(queue.get()), asyncio.create_task(closing.wait())]
    try:
        done, _ = await asyncio.wait(
            waits, timeout=STREAM_HEARTBEAT, return_when=asyncio.FIRST_COMPLETED
        )
        if not done:
            raise TimeoutError
        for task in waits:
            if task.done() and not task.cancelled():
                result = task.result()
                if isinstance(result, Tick):
                    return result
        return None
    finally:
        # A tick pulled from the queue by a task nobody read is a lost tick, but
        # this only happens on shutdown or a heartbeat, where losing one is fine.
        for task in waits:
            task.cancel()


def frame(tick: Tick) -> str:
    return (
        "data: "
        + json.dumps(
            {
                "symbol": tick.symbol,
                "price": tick.price,
                "change_pct": tick.change_pct,
                "at": tick.at.isoformat(),
            }
        )
        + "\n\n"
    )


async def tick_events(
    request: _Request,
    hub: TickHub | None,
    closing: asyncio.Event | None,
    symbols: Collection[str] | None = None,
) -> AsyncIterator[str]:
    """Every tick for `symbols` (all of them when None), the current picture first.

    Each reader gets its own bounded queue, unregistered when the reader goes -
    a browser closing a tab must not leave one growing behind it.
    """
    wanted = None if symbols is None else frozenset(symbols)
    if hub is None:
        # Said once, rather than holding a connection open that will never
        # carry anything: the stream never started.
        yield 'event: closed\ndata: {"reason":"no price stream"}\n\n'
        return
    with hub.subscribe() as queue:
        # The current picture first, so a page that has just loaded is not blank
        # until something moves.
        for symbol in hub.prices():
            known = hub.tick(symbol)
            if known is not None and (wanted is None or symbol in wanted):
                yield frame(known)
        while True:
            if await request.is_disconnected():
                return
            if closing is not None and closing.is_set():
                return
            try:
                tick = await next_tick(queue, closing)
            except TimeoutError:
                # A comment, which SSE ignores: it keeps the connection open
                # without the client having to filter a fake price.
                yield ": keep-alive\n\n"
                continue
            if tick is None:
                # Shutting down.
                return
            if wanted is None or tick.symbol in wanted:
                yield frame(tick)


def sse(events: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={
            # Without this a proxy will buffer the stream and deliver it in lumps,
            # which defeats the point.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

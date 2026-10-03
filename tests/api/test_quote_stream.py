"""The options desk's price stream: its own symbols only, the current picture first."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from starlette.requests import Request

from api.routers import market
from broker.models import Tick
from streaming import TickHub


class FakeRequest:
    def __init__(self, hub: TickHub | None, streams: dict[str, object] | None = None) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(tick_hub=hub, tick_streams=streams or {}))

    async def is_disconnected(self) -> bool:
        return False


async def _first_frames(hub: TickHub, count: int) -> list[str]:
    response = await market.quote_stream(cast(Request, FakeRequest(hub)))
    events = cast(AsyncGenerator[str, None], response.body_iterator)
    return [await asyncio.wait_for(anext(events), timeout=2) for _ in range(count)]


def test_it_sends_the_indices_and_not_the_perpetuals() -> None:
    hub = TickHub()
    now = datetime.now(UTC)
    hub.publish(Tick(symbol="BTCUSDT", price=84_000.0, at=now))
    hub.publish(Tick(symbol="NSE:NIFTY50-INDEX", price=22_421.95, at=now, change_pct=0.1))
    hub.publish(Tick(symbol="NSE:INDIAVIX-INDEX", price=14.46, at=now))

    frames = asyncio.run(_first_frames(hub, 2))
    joined = "".join(frames)
    assert "NIFTY50" in joined
    assert "INDIAVIX" in joined
    assert "BTCUSDT" not in joined


def test_a_later_index_tick_is_pushed_and_a_perp_tick_is_not() -> None:
    hub = TickHub()

    async def run() -> str:
        response = await market.quote_stream(cast(Request, FakeRequest(hub)))
        events = cast(AsyncGenerator[str, None], response.body_iterator)
        task: asyncio.Task[str] = asyncio.create_task(anext(events))
        await asyncio.sleep(0)
        now = datetime.now(UTC)
        hub.publish(Tick(symbol="BTCUSDT", price=84_000.0, at=now))
        hub.publish(Tick(symbol="NSE:NIFTYBANK-INDEX", price=54_450.75, at=now))
        return await asyncio.wait_for(task, timeout=2)

    frame = asyncio.run(run())
    assert "NIFTYBANK" in frame


class WatchRecorder:
    """Stands in for the venue's stream: records what a reader watched."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    @asynccontextmanager
    async def watching(self, symbols: list[str]) -> AsyncIterator[None]:
        self.calls.append(("watch", list(symbols)))
        try:
            yield
        finally:
            self.calls.append(("unwatch", list(symbols)))


def test_asked_for_contracts_are_watched_while_read_and_released_after() -> None:
    hub = TickHub()
    recorder = WatchRecorder()
    leg = "NSE:NIFTY26OCT23000CE"

    async def run() -> str:
        request = FakeRequest(hub, {"fyers": recorder})
        response = await market.quote_stream(
            cast(Request, request), symbols=f"{leg},not a symbol,{leg}"
        )
        events = cast(AsyncGenerator[str, None], response.body_iterator)
        task: asyncio.Task[str] = asyncio.create_task(anext(events))
        await asyncio.sleep(0)
        hub.publish(Tick(symbol=leg, price=120.5, at=datetime.now(UTC)))
        frame = await asyncio.wait_for(task, timeout=2)
        await events.aclose()
        return frame

    frame = asyncio.run(run())
    assert leg in frame
    assert recorder.calls == [("watch", [leg]), ("unwatch", [leg])]

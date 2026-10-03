"""Word of account changes, pushed to the page."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from starlette.requests import Request

from api.routers import portfolio
from broker.fyers.account_stream import AccountEvent
from streaming.account import AccountHub


class FakeRequest:
    def __init__(self, hub: AccountHub | None, connected: bool) -> None:
        stream = SimpleNamespace(connected=connected)
        self.app = SimpleNamespace(state=SimpleNamespace(account_hub=hub, account_stream=stream))

    async def is_disconnected(self) -> bool:
        return False


async def _events(request: FakeRequest) -> AsyncGenerator[str, None]:
    response = await portfolio.portfolio_stream(cast(Request, request))
    return cast(AsyncGenerator[str, None], response.body_iterator)


def test_it_says_when_the_account_socket_is_down() -> None:
    async def run() -> list[str]:
        events = await _events(FakeRequest(AccountHub(), connected=False))
        return [frame async for frame in events]

    frames = asyncio.run(run())
    assert frames == ['event: ready\ndata: {"live": false}\n\n']


def test_a_fill_is_pushed() -> None:
    hub = AccountHub()

    async def run() -> str:
        events = await _events(FakeRequest(hub, connected=True))
        ready = await anext(events)
        assert '"live": true' in ready
        task: asyncio.Task[str] = asyncio.create_task(anext(events))
        await asyncio.sleep(0)
        hub.publish(AccountEvent(kind="trades", at=datetime.now(UTC)))
        frame = await asyncio.wait_for(task, timeout=2)
        await events.aclose()
        return frame

    assert '"kind": "trades"' in asyncio.run(run())

"""What the account holds, and how P&L has moved."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from api.deps import BrokerDep, DbPathDep, HolidaysDep
from api.schemas import (
    PortfolioHistoryPoint,
    PortfolioResponse,
)
from api.store import open_db
from execution.portfolio_status import PortfolioStatus, get_portfolio_status
from feeds.holidays import Holidays
from storage.portfolio_repo import save_portfolio_snapshot, snapshots_since
from streaming.account import AccountHub
from streaming.sse import STREAM_HEARTBEAT, sse
from venues.calendar import in_session

router = APIRouter()


SNAPSHOT_EVERY = timedelta(minutes=2)

_last_snapshot: datetime | None = None


@router.get("/api/portfolio", response_model=PortfolioResponse)
def portfolio(broker: BrokerDep, db_path: DbPathDep, holidays: HolidaysDep) -> PortfolioResponse:
    status = get_portfolio_status(broker)
    _record_snapshot(status, db_path, holidays)
    return PortfolioResponse(
        positions=status.positions,
        realized_pnl=status.realized_pnl,
        unrealized_pnl=status.unrealized_pnl,
        total_pnl=status.total_pnl,
    )


def _record_snapshot(status: PortfolioStatus, db_path: Path, holidays: Holidays) -> None:
    """Keep a throttled history of P&L, for the chart that plots it.

    Recorded here rather than by a background task on purpose: it costs no
    extra broker call, because it stores what was just fetched anyway, and it
    only accumulates while somebody is actually watching. A timer would keep
    polling an empty room.

    Nothing is recorded while the exchange is shut. Prices do not move
    overnight, at weekends or on a holiday, so a snapshot then is a duplicate
    of the close - it would pad the table and draw a flat line across hours
    when nothing was happening.

    Never allowed to fail the request - the P&L on screen matters more than
    the history behind it.
    """
    global _last_snapshot
    now = datetime.now(UTC)
    if not in_session(now, holidays.dates()):
        return
    if _last_snapshot is not None and now - _last_snapshot < SNAPSHOT_EVERY:
        return
    try:
        conn = open_db(db_path)
        save_portfolio_snapshot(
            conn,
            status.positions,
            status.realized_pnl,
            status.unrealized_pnl,
            fetched_at=now,
        )
        conn.close()
        _last_snapshot = now
    except sqlite3.Error:
        pass


@router.get("/api/portfolio/history", response_model=list[PortfolioHistoryPoint])
def portfolio_history(db_path: DbPathDep, days: int = 7) -> list[PortfolioHistoryPoint]:
    conn = open_db(db_path)
    since = datetime.now(UTC) - timedelta(days=days)
    rows = snapshots_since(conn, since=since)
    conn.close()
    return [
        PortfolioHistoryPoint(
            fetched_at=row.fetched_at.isoformat(),
            realized_pnl=row.realized_pnl,
            unrealized_pnl=row.unrealized_pnl,
            total_pnl=row.realized_pnl + row.unrealized_pnl,
        )
        for row in rows
    ]


@router.get("/api/portfolio/stream")
async def portfolio_stream(request: Request) -> StreamingResponse:
    """Word that the account changed: an order, a fill, a position.

    No figures - each event means "read /api/portfolio again". The first frame
    says whether the venue's account socket is up at all, so a page knows
    whether it can stop polling.
    """
    hub = getattr(request.app.state, "account_hub", None)
    stream = getattr(request.app.state, "account_stream", None)
    live = stream is not None and bool(getattr(stream, "connected", False))
    closing: asyncio.Event | None = getattr(request.app.state, "shutting_down", None)

    async def events() -> AsyncIterator[str]:
        ready = live and isinstance(hub, AccountHub)
        yield f"event: ready\ndata: {json.dumps({'live': ready})}\n\n"
        if not isinstance(hub, AccountHub) or not ready:
            return
        with hub.subscribe() as queue:
            while True:
                if await request.is_disconnected():
                    return
                if closing is not None and closing.is_set():
                    return
                got = asyncio.create_task(queue.get())
                waits: list[asyncio.Task[object]] = [got]
                if closing is not None:
                    waits.append(asyncio.create_task(closing.wait()))
                done, pending = await asyncio.wait(
                    waits, timeout=STREAM_HEARTBEAT, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                if not done:
                    yield ": keep-alive\n\n"
                    continue
                if not got.done() or got.cancelled():
                    return
                event = got.result()
                yield f"data: {json.dumps({'kind': event.kind, 'at': event.at.isoformat()})}\n\n"

    return sse(events())

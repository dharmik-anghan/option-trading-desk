"""What the account holds, and how P&L has moved.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter

from api.deps import BrokerDep, DbPathDep, HolidaysDep
from api.schemas import (
    PortfolioHistoryPoint,
    PortfolioResponse,
)
from api.store import open_db
from execution.portfolio_status import PortfolioStatus, get_portfolio_status
from feeds.holidays import Holidays
from storage.portfolio_repo import save_portfolio_snapshot, snapshots_since
from venues.calendar import in_session

router = APIRouter()


SNAPSHOT_EVERY = timedelta(minutes=2)

_last_snapshot: datetime | None = None

@router.get("/api/portfolio", response_model=PortfolioResponse)
def portfolio(
    broker: BrokerDep, db_path: DbPathDep, holidays: HolidaysDep
) -> PortfolioResponse:
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

"""Persistence for portfolio P&L snapshots — the history behind a P&L-over-
time view, same pattern as `storage/option_chain_repo.py`.

Deliberately depends only on `broker.models` (like every other `storage/`
module), not on `execution.portfolio_status.PortfolioStatus` — storage sits
at the bottom of the dependency direction (strategies/risk/execution depend
on it downward, never the reverse), so it works with plain positions +
P&L numbers and lets callers assemble whatever aggregate type they need.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from broker.models import Position


def save_portfolio_snapshot(
    conn: sqlite3.Connection,
    positions: list[Position],
    realized_pnl: float,
    unrealized_pnl: float,
    *,
    fetched_at: datetime,
) -> int:
    cursor = conn.execute(
        "INSERT INTO portfolio_snapshot (fetched_at, realized_pnl, unrealized_pnl) "
        "VALUES (?, ?, ?)",
        (fetched_at.isoformat(), realized_pnl, unrealized_pnl),
    )
    snapshot_id = cursor.lastrowid
    assert snapshot_id is not None

    conn.executemany(
        "INSERT INTO portfolio_position "
        "(snapshot_id, symbol, net_quantity, average_price, ltp, unrealized_pnl, product_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                snapshot_id,
                p.symbol,
                p.net_quantity,
                p.average_price,
                p.ltp,
                p.unrealized_pnl,
                p.product_type,
            )
            for p in positions
        ],
    )
    conn.commit()
    return snapshot_id


@dataclass(frozen=True)
class PortfolioSnapshotRow:
    fetched_at: datetime
    positions: list[Position]
    realized_pnl: float
    unrealized_pnl: float


def snapshots_since(conn: sqlite3.Connection, *, since: datetime) -> list[PortfolioSnapshotRow]:
    snapshot_rows = conn.execute(
        "SELECT id, fetched_at, realized_pnl, unrealized_pnl FROM portfolio_snapshot "
        "WHERE fetched_at >= ? ORDER BY fetched_at ASC",
        (since.isoformat(),),
    ).fetchall()

    results: list[PortfolioSnapshotRow] = []
    for snapshot_id, fetched_at, realized_pnl, unrealized_pnl in snapshot_rows:
        position_rows = conn.execute(
            "SELECT symbol, net_quantity, average_price, ltp, unrealized_pnl, product_type "
            "FROM portfolio_position WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()
        positions = [
            Position(
                symbol=r[0],
                net_quantity=r[1],
                average_price=r[2],
                ltp=r[3],
                unrealized_pnl=r[4],
                product_type=r[5],
            )
            for r in position_rows
        ]
        results.append(
            PortfolioSnapshotRow(
                fetched_at=datetime.fromisoformat(fetched_at),
                positions=positions,
                realized_pnl=realized_pnl,
                unrealized_pnl=unrealized_pnl,
            )
        )
    return results

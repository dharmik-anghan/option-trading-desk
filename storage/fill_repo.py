"""The broker fills the desk has seen, and what each one did.

A sync reads fills from the broker and writes here what it made of them, so
running it again finds nothing new to apply. Pending fills - ones that opened
a position no structure holds - wait here until they are assigned or ignored.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from broker.models import Fill

#: What a fill meant. See migration 8 for each.
STATUSES = ("applied", "covered", "pending", "outside", "ignored", "detached")


@dataclass(frozen=True)
class StoredFill:
    fill_id: str
    order_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    at: datetime
    status: str
    basket_id: int | None
    leg_id: int | None
    action: str | None


def seen(conn: sqlite3.Connection, fill_ids: Iterable[str]) -> set[str]:
    ids = list(fill_ids)
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT fill_id FROM broker_fill WHERE fill_id IN ({marks})", ids)
    return {row[0] for row in rows.fetchall()}


def record(
    conn: sqlite3.Connection,
    fill: Fill,
    status: str,
    *,
    seen_at: datetime,
    basket_id: int | None = None,
    leg_id: int | None = None,
    action: str | None = None,
    fill_id: str | None = None,
    quantity: int | None = None,
) -> None:
    """Note a fill and what it meant. `fill_id` and `quantity` override the
    fill's own for the part of a fill left over after it closed a leg."""
    assert status in STATUSES
    conn.execute(
        "INSERT INTO broker_fill (fill_id, order_id, symbol, side, quantity, price, at, "
        "status, basket_id, leg_id, action, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            fill_id or fill.fill_id,
            fill.order_id,
            fill.symbol,
            fill.side,
            int(quantity if quantity is not None else fill.quantity),
            fill.price,
            fill.at.isoformat(),
            status,
            basket_id,
            leg_id,
            action,
            seen_at.isoformat(),
        ),
    )
    conn.commit()


def _row(row: tuple[object, ...]) -> StoredFill:
    return StoredFill(
        fill_id=str(row[0]),
        order_id=str(row[1]),
        symbol=str(row[2]),
        side=str(row[3]),
        quantity=int(row[4]),  # type: ignore[call-overload]
        price=float(row[5]),  # type: ignore[arg-type]
        at=datetime.fromisoformat(str(row[6])),
        status=str(row[7]),
        basket_id=row[8],  # type: ignore[arg-type]
        leg_id=row[9],  # type: ignore[arg-type]
        action=row[10],  # type: ignore[arg-type]
    )


_COLUMNS = (
    "fill_id, order_id, symbol, side, quantity, price, at, status, basket_id, leg_id, action"
)


def with_status(conn: sqlite3.Connection, status: str) -> list[StoredFill]:
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM broker_fill WHERE status = ? ORDER BY at", (status,)
    ).fetchall()
    return [_row(r) for r in rows]


def get(conn: sqlite3.Connection, fill_ids: Iterable[str]) -> list[StoredFill]:
    ids = list(fill_ids)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM broker_fill WHERE fill_id IN ({marks}) ORDER BY at", ids
    ).fetchall()
    return [_row(r) for r in rows]


def for_basket(conn: sqlite3.Connection, basket_id: int) -> list[StoredFill]:
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM broker_fill WHERE basket_id = ? ORDER BY at", (basket_id,)
    ).fetchall()
    return [_row(r) for r in rows]


def settle(
    conn: sqlite3.Connection,
    fill_id: str,
    status: str,
    *,
    basket_id: int | None = None,
    leg_id: int | None = None,
    action: str | None = None,
) -> None:
    """Mark a pending fill as dealt with: assigned to a leg, or ignored."""
    assert status in STATUSES
    conn.execute(
        "UPDATE broker_fill SET status = ?, basket_id = ?, leg_id = ?, action = ? "
        "WHERE fill_id = ?",
        (status, basket_id, leg_id, action, fill_id),
    )
    conn.commit()

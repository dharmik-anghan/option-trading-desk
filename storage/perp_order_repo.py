"""The record of every perpetual order this program formed.

Written before the attempt, not after the reply, and written for refusals and
rehearsals too. After a surprise the question is "what did it try to do", and a
log kept only on success cannot answer that.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class PerpOrderRecord:
    id: int
    at: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: float | None
    leverage: float
    notional: float
    #: False for a refusal or a dry run; `reason` says which.
    sent: bool
    reason: str
    venue_order_id: str | None


def record_order(
    conn: sqlite3.Connection,
    *,
    at: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: float | None,
    leverage: float,
    notional: float,
    sent: bool,
    reason: str,
    venue_order_id: str | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO perp_order (at, symbol, side, order_type, quantity, price, leverage, "
        "notional, sent, reason, venue_order_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            at,
            symbol,
            side,
            order_type,
            quantity,
            price,
            leverage,
            notional,
            1 if sent else 0,
            reason,
            venue_order_id,
        ),
    )
    conn.commit()
    row_id = cursor.lastrowid
    assert row_id is not None
    return row_id


def note_outcome(
    conn: sqlite3.Connection, order_id: int, *, sent: bool, reason: str, venue_order_id: str | None
) -> None:
    """Fill in what happened, once the venue has answered.

    Separate from `record_order` so the attempt is on disk before the request
    leaves. If the process dies mid-send there is a row saying an order was
    attempted, which is the thing worth knowing.
    """
    conn.execute(
        "UPDATE perp_order SET sent = ?, reason = ?, venue_order_id = ? WHERE id = ?",
        (1 if sent else 0, reason, venue_order_id, order_id),
    )
    conn.commit()


def recent_orders(conn: sqlite3.Connection, limit: int = 50) -> list[PerpOrderRecord]:
    """Newest first."""
    rows = conn.execute(
        "SELECT id, at, symbol, side, order_type, quantity, price, leverage, notional, "
        "sent, reason, venue_order_id FROM perp_order ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        PerpOrderRecord(
            id=r[0],
            at=r[1],
            symbol=r[2],
            side=r[3],
            order_type=r[4],
            quantity=r[5],
            price=r[6],
            leverage=r[7],
            notional=r[8],
            sent=bool(r[9]),
            reason=r[10],
            venue_order_id=r[11],
        )
        for r in rows
    ]

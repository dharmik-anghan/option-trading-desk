"""Persistence for baskets: the source of truth for "this group of legs is
one strategy," tracked by us rather than inferred from live broker state.

A basket keeps every leg ever part of it - including closed ones - so a
strategy's economics (Phase-9-follow-up: combined payoff) can include P&L
already banked from legs you've since exited, not just what's still open.
This also sidesteps trying to auto-group by underlying+expiry, which breaks
for calendar spreads (legs at different expiries, same strategy).

Depends only on `broker.models` types (like every other `storage/` module)
so it stays at the bottom of the dependency direction.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from broker.models import OptionType, Side


@dataclass(frozen=True)
class NewBasketLeg:
    symbol: str
    option_type: OptionType
    strike: float
    side: Side
    quantity: int
    entry_price: float
    entry_at: datetime | None = None  # defaults to the basket's created_at


@dataclass(frozen=True)
class BasketLeg:
    id: int
    symbol: str
    option_type: OptionType
    strike: float
    side: Side
    quantity: int
    entry_price: float
    entry_at: datetime
    exit_price: float | None
    exit_at: datetime | None

    @property
    def is_open(self) -> bool:
        return self.exit_price is None


@dataclass(frozen=True)
class Basket:
    id: int
    name: str
    strategy: str
    underlying_symbol: str
    created_at: datetime
    stop_loss: float | None
    legs: list[BasketLeg]


def create_basket(
    conn: sqlite3.Connection,
    name: str,
    strategy: str,
    underlying_symbol: str,
    legs: list[NewBasketLeg],
    created_at: datetime,
    stop_loss: float | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO basket (name, strategy, underlying_symbol, created_at, stop_loss) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, strategy, underlying_symbol, created_at.isoformat(), stop_loss),
    )
    basket_id = cursor.lastrowid
    assert basket_id is not None

    conn.executemany(
        "INSERT INTO basket_leg "
        "(basket_id, symbol, option_type, strike, side, quantity, entry_price, entry_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                basket_id,
                leg.symbol,
                leg.option_type,
                leg.strike,
                leg.side,
                leg.quantity,
                leg.entry_price,
                (leg.entry_at or created_at).isoformat(),
            )
            for leg in legs
        ],
    )
    conn.commit()
    return basket_id


def _row_to_leg(row: tuple[object, ...]) -> BasketLeg:
    (
        leg_id,
        symbol,
        option_type,
        strike,
        side,
        quantity,
        entry_price,
        entry_at,
        exit_price,
        exit_at,
    ) = row
    return BasketLeg(
        id=leg_id,  # type: ignore[arg-type]
        symbol=symbol,  # type: ignore[arg-type]
        option_type=option_type,  # type: ignore[arg-type]
        strike=strike,  # type: ignore[arg-type]
        side=side,  # type: ignore[arg-type]
        quantity=quantity,  # type: ignore[arg-type]
        entry_price=entry_price,  # type: ignore[arg-type]
        entry_at=datetime.fromisoformat(entry_at),  # type: ignore[arg-type]
        exit_price=exit_price,  # type: ignore[arg-type]
        exit_at=datetime.fromisoformat(exit_at) if exit_at else None,  # type: ignore[arg-type]
    )


def _legs_for_basket(conn: sqlite3.Connection, basket_id: int) -> list[BasketLeg]:
    rows = conn.execute(
        "SELECT id, symbol, option_type, strike, side, quantity, entry_price, entry_at, "
        "       exit_price, exit_at "
        "FROM basket_leg WHERE basket_id = ? ORDER BY id ASC",
        (basket_id,),
    ).fetchall()
    return [_row_to_leg(row) for row in rows]


def get_basket(conn: sqlite3.Connection, basket_id: int) -> Basket | None:
    row = conn.execute(
        "SELECT id, name, strategy, underlying_symbol, created_at, stop_loss "
        "FROM basket WHERE id = ?",
        (basket_id,),
    ).fetchone()
    if row is None:
        return None
    return Basket(
        id=row[0],
        name=row[1],
        strategy=row[2],
        underlying_symbol=row[3],
        created_at=datetime.fromisoformat(row[4]),
        stop_loss=row[5],
        legs=_legs_for_basket(conn, basket_id),
    )


def list_baskets(conn: sqlite3.Connection) -> list[Basket]:
    rows = conn.execute("SELECT id FROM basket ORDER BY created_at ASC").fetchall()
    baskets = [get_basket(conn, row[0]) for row in rows]
    return [b for b in baskets if b is not None]


def close_leg(conn: sqlite3.Connection, leg_id: int, exit_price: float, exit_at: datetime) -> None:
    conn.execute(
        "UPDATE basket_leg SET exit_price = ?, exit_at = ? WHERE id = ?",
        (exit_price, exit_at.isoformat(), leg_id),
    )
    conn.commit()

def delete_basket(conn: sqlite3.Connection, basket_id: int) -> bool:
    """Erase a basket and every leg in it.

    This is a bookkeeping correction, not an exit: it removes our record of
    the grouping and nothing else. Whatever is open at the broker stays open.
    Legs go first because `db.connect` turns foreign keys on.

    Returns False when there was no such basket, so the caller can 404.
    """
    if conn.execute("SELECT 1 FROM basket WHERE id = ?", (basket_id,)).fetchone() is None:
        return False
    conn.execute("DELETE FROM basket_leg WHERE basket_id = ?", (basket_id,))
    conn.execute("DELETE FROM basket WHERE id = ?", (basket_id,))
    conn.commit()
    return True


def delete_leg(conn: sqlite3.Connection, basket_id: int, leg_id: int) -> bool:
    """Drop one leg out of a basket, for when the grouping was wrong.

    Use `close_leg` instead when the leg was actually exited - that keeps it
    on the basket with its exit price, which the basket's economics rely on.
    A basket left with no legs is deleted too, rather than lingering as an
    empty card.

    `basket_id` is matched as well as `leg_id` so a stale id from one basket
    can never delete a leg out of another.
    """
    cursor = conn.execute(
        "DELETE FROM basket_leg WHERE id = ? AND basket_id = ?", (leg_id, basket_id)
    )
    if cursor.rowcount == 0:
        conn.commit()
        return False
    remaining = conn.execute(
        "SELECT COUNT(*) FROM basket_leg WHERE basket_id = ?", (basket_id,)
    ).fetchone()[0]
    if remaining == 0:
        conn.execute("DELETE FROM basket WHERE id = ?", (basket_id,))
    conn.commit()
    return True

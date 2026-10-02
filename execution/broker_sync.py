"""Keeping structures in step with what the broker actually executed.

A structure is ours - the broker has no idea four legs are one iron condor - but
what happens to its legs happens at the broker. Before this, a leg bought back
in the Fyers app stayed open on the desk until someone noticed and typed in an
exit price. Now each fill is read, once, and applied:

  A fill on the opposite side of an open leg closes it, at the fill's price and
  time - partly, if it was for less. Automatic: there is no other thing it can
  mean.

  A fill that a leg already records (the leg was entered from this very trade,
  or adopted from the position it built) is `covered`, and changes nothing.

  A fill that opened something the account still holds, and no structure has,
  is `pending`. It is never put into a structure on a guess. It waits, with a
  suggestion of which structure it probably belongs to - the one on the same
  expiry that had a leg closed within minutes of it, which is what an
  adjustment looks like - and you confirm or choose.

  Anything else is `outside`: a round trip that never touched a structure.

Read-only toward the broker. The only calls made are `get_fills` and
`get_positions`; this module is handed an object that offers nothing more, and
a test runs it against a broker whose `place_order` fails loudly.

Open legs the broker no longer holds, with no fill to explain it, are reported
and left alone: a missing fill is not an exit price, and a guessed one would
quietly corrupt the structure's realized P&L.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

from broker.base import FillHistory
from broker.contracts import ContractCodec
from broker.models import Fill, Position
from storage import fill_repo
from storage.basket_repo import (
    Basket,
    BasketLeg,
    NewBasketLeg,
    add_leg,
    close_quantity,
    create_basket,
    list_baskets,
)

#: A fill this close to a leg's recorded entry is the trade that leg records.
#: Adopted legs carry the time they were adopted, not the time they traded, so
#: the check is "the leg was recorded at or after the fill".
SAME_TRADE = timedelta(minutes=5)

#: Fills this close together on one structure are one adjustment.
ADJUSTMENT_WINDOW = timedelta(minutes=30)


class FillSource(FillHistory, Protocol):
    """All the sync may ask of a broker: its fills, and what is open now."""

    def get_positions(self) -> list[Position]: ...


@dataclass(frozen=True)
class Applied:
    fill: Fill
    basket_id: int
    basket_name: str
    leg_id: int
    quantity: int
    realized: float


@dataclass(frozen=True)
class Suggestion:
    basket_id: int
    basket_name: str
    why: str


@dataclass(frozen=True)
class Pending:
    fill_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    at: datetime
    suggestion: Suggestion | None


@dataclass(frozen=True)
class Unexplained:
    """An open leg the broker no longer holds, with no fill found to close it."""

    basket_id: int
    basket_name: str
    leg: BasketLeg
    broker_quantity: float


@dataclass(frozen=True)
class SyncReport:
    since: date
    fills_read: int
    already_seen: int
    closed: list[Applied]
    covered: int
    outside: int
    pending: list[Pending]
    unexplained: list[Unexplained]


def _signed(side: str) -> int:
    return 1 if side == "BUY" else -1


def _realized(leg: BasketLeg, quantity: int, exit_price: float) -> float:
    return _signed(leg.side) * (exit_price - leg.entry_price) * quantity


def sync(
    conn: sqlite3.Connection,
    source: FillSource,
    *,
    codec: ContractCodec,
    since: date,
    until: date,
    now: datetime,
) -> SyncReport:
    fills = source.get_fills(since, until)
    positions = {p.symbol: p.net_quantity for p in source.get_positions()}
    seen = fill_repo.seen(conn, [f.fill_id for f in fills])

    closed: list[Applied] = []
    covered = outside = 0
    for fill in fills:
        if fill.fill_id in seen:
            continue
        baskets = list_baskets(conn)
        applied = _close_from(conn, fill, baskets, now)
        if applied:
            closed.extend(applied)
            continue
        leg_owner = _covering_leg(fill, baskets)
        if leg_owner is not None:
            basket, leg = leg_owner
            fill_repo.record(
                conn, fill, "covered", seen_at=now, basket_id=basket.id, leg_id=leg.id,
                action="open",
            )
            covered += 1
            continue
        held = positions.get(fill.symbol, 0.0)
        if held * _signed(fill.side) > 0:
            fill_repo.record(conn, fill, "pending", seen_at=now)
        else:
            fill_repo.record(conn, fill, "outside", seen_at=now)
            outside += 1

    baskets = list_baskets(conn)
    return SyncReport(
        since=since,
        fills_read=len(fills),
        already_seen=len(seen),
        closed=closed,
        covered=covered,
        outside=outside,
        pending=pending_with_suggestions(conn, baskets, codec),
        unexplained=_unexplained(baskets, positions),
    )


def _close_from(
    conn: sqlite3.Connection, fill: Fill, baskets: list[Basket], now: datetime
) -> list[Applied]:
    """Close open legs on the other side of this fill, oldest first."""
    candidates = sorted(
        (
            (basket, leg)
            for basket in baskets
            for leg in basket.legs
            if leg.is_open
            and leg.symbol == fill.symbol
            and leg.side != fill.side
            # A close cannot come before the leg existed.
            and leg.entry_at <= fill.at + SAME_TRADE
        ),
        key=lambda pair: pair[1].entry_at,
    )
    if not candidates:
        return []
    remaining = int(fill.quantity)
    out: list[Applied] = []
    for basket, leg in candidates:
        if remaining <= 0:
            break
        quantity = min(remaining, leg.quantity)
        closed_id = close_quantity(conn, leg.id, quantity, fill.price, fill.at)
        out.append(
            Applied(fill, basket.id, basket.name, closed_id, quantity,
                    _realized(leg, quantity, fill.price))
        )
        remaining -= quantity
    first = out[0]
    fill_repo.record(
        conn, fill, "applied", seen_at=now, basket_id=first.basket_id, leg_id=first.leg_id,
        action="close", quantity=int(fill.quantity) - remaining,
    )
    if remaining > 0:
        # More was bought (or sold) than the structure held: the rest opened a
        # position the other way, and waits like any other new position.
        fill_repo.record(
            conn, fill, "pending", seen_at=now, fill_id=f"{fill.fill_id}+rest",
            quantity=remaining,
        )
    return out


def _covering_leg(fill: Fill, baskets: list[Basket]) -> tuple[Basket, BasketLeg] | None:
    """The leg that already records this fill, if one does."""
    for basket in baskets:
        for leg in basket.legs:
            if (
                leg.symbol == fill.symbol
                and leg.side == fill.side
                and leg.entry_at >= fill.at - SAME_TRADE
            ):
                return basket, leg
    return None


def _unexplained(baskets: list[Basket], positions: dict[str, float]) -> list[Unexplained]:
    """Open legs whose contract the broker holds less of, in that direction, than
    the structures say. Reported; never closed on a guess."""
    wanted: dict[str, float] = defaultdict(float)
    owners: dict[str, list[tuple[Basket, BasketLeg]]] = defaultdict(list)
    for basket in baskets:
        for leg in basket.legs:
            if leg.is_open:
                wanted[leg.symbol] += _signed(leg.side) * leg.quantity
                owners[leg.symbol].append((basket, leg))
    out = []
    for symbol, need in wanted.items():
        held = positions.get(symbol, 0.0)
        if need * held <= 0 or abs(held) < abs(need):
            for basket, leg in owners[symbol]:
                out.append(Unexplained(basket.id, basket.name, leg, held))
    return out


def suggest(
    fill: fill_repo.StoredFill, baskets: list[Basket], codec: ContractCodec
) -> Suggestion | None:
    """Which structure a new position most likely belongs to.

    Same expiry first, then the one that had a leg closed nearest in time -
    buying back one spread and selling another a minute later is an adjustment,
    and the structure it adjusts is the one that lost the leg.
    """
    parsed = codec.parse_contract(fill.symbol)
    if parsed is None:
        return None
    series = parsed[0]
    best: tuple[timedelta, Basket] | None = None
    for basket in baskets:
        legs = [
            leg for leg in basket.legs if (p := codec.parse_contract(leg.symbol)) and p[0] == series
        ]
        if not legs or not any(leg.is_open for leg in basket.legs):
            continue
        gaps = [
            abs(leg.exit_at - fill.at) for leg in legs if leg.exit_at is not None
        ]
        gap = min(gaps) if gaps else timedelta.max
        if best is None or gap < best[0]:
            best = (gap, basket)
    if best is None:
        return None
    gap, basket = best
    if gap <= ADJUSTMENT_WINDOW:
        minutes = max(1, round(gap.total_seconds() / 60))
        why = f"a leg of it was closed {minutes} min from this fill - looks like an adjustment"
    else:
        why = "open structure on the same expiry"
    return Suggestion(basket.id, basket.name, why)


def pending_with_suggestions(
    conn: sqlite3.Connection, baskets: list[Basket], codec: ContractCodec
) -> list[Pending]:
    return [
        Pending(f.fill_id, f.symbol, f.side, f.quantity, f.price, f.at, suggest(f, baskets, codec))
        for f in fill_repo.with_status(conn, "pending")
    ]


# ------------------------------------------------------------ settling pending


def _leg_from(fill: fill_repo.StoredFill, codec: ContractCodec) -> NewBasketLeg:
    parsed = codec.parse_contract(fill.symbol)
    if parsed is None:
        raise ValueError(f"{fill.symbol} is not an option contract")
    _, strike, kind = parsed
    return NewBasketLeg(
        symbol=fill.symbol,
        option_type=kind,
        strike=strike,
        side="BUY" if fill.side == "BUY" else "SELL",
        quantity=fill.quantity,
        entry_price=fill.price,
        entry_at=fill.at,
    )


def assign(
    conn: sqlite3.Connection,
    fill_ids: list[str],
    *,
    codec: ContractCodec,
    basket_id: int | None = None,
    new_name: str | None = None,
    new_strategy: str = "Custom",
    underlying_symbol: str = "NSE:NIFTY50-INDEX",
    now: datetime,
) -> int:
    """Put pending fills into a structure, as new legs. Returns the structure's id.

    Either an existing structure (`basket_id`), or a new one named `new_name`.
    Each fill becomes its own leg, entered at the fill's own price and time.
    """
    fills = [f for f in fill_repo.get(conn, fill_ids) if f.status == "pending"]
    if len(fills) != len(set(fill_ids)):
        raise ValueError("only pending fills can be assigned")
    legs = [_leg_from(f, codec) for f in fills]
    if basket_id is None:
        if not new_name:
            raise ValueError("name the new structure, or pick an existing one")
        basket_id = create_basket(
            conn, new_name, new_strategy, underlying_symbol, [], created_at=legs[0].entry_at or now
        )
    for fill, leg in zip(fills, legs, strict=True):
        leg_id = add_leg(conn, basket_id, leg)
        fill_repo.settle(conn, fill.fill_id, "applied", basket_id=basket_id, leg_id=leg_id,
                         action="open")
    return basket_id


def ignore(conn: sqlite3.Connection, fill_ids: list[str]) -> None:
    for fill in fill_repo.get(conn, fill_ids):
        if fill.status == "pending":
            fill_repo.settle(conn, fill.fill_id, "ignored")

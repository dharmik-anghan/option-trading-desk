"""A structure's story: when it was put on, each time it was adjusted, when it
came off, and what each step banked.

Built from the legs themselves - every leg carries when it was opened and, if
closed, when and at what - so it works for structures recorded long before
fills were synced, and needs no second record that could disagree with the
first.

Leg openings and closings that happen close together are one moment: buying
back a call spread and selling a lower one a minute later is a single
adjustment, and reads as one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from storage.basket_repo import Basket, BasketLeg

#: Leg events closer together than this are one moment.
MOMENT = timedelta(minutes=30)

Kind = Literal["opened", "adjusted", "added", "reduced", "closed"]


@dataclass(frozen=True)
class LegEvent:
    at: datetime
    action: Literal["open", "close"]
    leg_id: int
    symbol: str
    side: str
    quantity: int
    price: float
    #: For a close: what that leg banked.
    realized: float | None = None


@dataclass
class Moment:
    at: datetime
    kind: Kind
    events: list[LegEvent] = field(default_factory=list)
    #: Banked by the closes in this moment.
    realized: float = 0.0
    #: Premium in (+) or out (-) by the opens in this moment, per unit x quantity.
    premium: float = 0.0
    #: Everything banked by the structure up to and including this moment.
    realized_to_date: float = 0.0


def _signed(side: str) -> int:
    return 1 if side == "BUY" else -1


def leg_events(leg: BasketLeg) -> list[LegEvent]:
    out = [
        LegEvent(leg.entry_at, "open", leg.id, leg.symbol, leg.side, leg.quantity,
                 leg.entry_price)
    ]
    if leg.exit_at is not None and leg.exit_price is not None:
        realized = _signed(leg.side) * (leg.exit_price - leg.entry_price) * leg.quantity
        out.append(
            LegEvent(leg.exit_at, "close", leg.id, leg.symbol, leg.side, leg.quantity,
                     leg.exit_price, realized)
        )
    return out


def history(basket: Basket) -> list[Moment]:
    events = sorted(
        (e for leg in basket.legs for e in leg_events(leg)), key=lambda e: (e.at, e.action)
    )
    # A partial close splits one leg into two rows that share an entry: the
    # opening shows once, for the whole quantity.
    merged: list[LegEvent] = []
    for e in events:
        prior = merged[-1] if merged else None
        if (
            prior is not None
            and e.action == "open"
            and prior.action == "open"
            and prior.symbol == e.symbol
            and prior.side == e.side
            and prior.at == e.at
            and prior.price == e.price
        ):
            merged[-1] = LegEvent(prior.at, "open", prior.leg_id, prior.symbol, prior.side,
                                  prior.quantity + e.quantity, prior.price)
        else:
            merged.append(e)

    moments: list[Moment] = []
    for e in merged:
        if not moments or e.at - moments[-1].events[-1].at > MOMENT:
            moments.append(Moment(at=e.at, kind="opened"))
        m = moments[-1]
        m.events.append(e)
        if e.action == "close":
            m.realized += e.realized or 0.0
        else:
            m.premium += -_signed(e.side) * e.price * e.quantity

    # Counted in contracts, not legs: closing 65 of 130 leaves the leg open.
    open_after = 0
    banked = 0.0
    for i, m in enumerate(moments):
        opens = sum(1 for e in m.events if e.action == "open")
        closes = sum(1 for e in m.events if e.action == "close")
        open_after += sum(e.quantity if e.action == "open" else -e.quantity for e in m.events)
        banked += m.realized
        m.realized_to_date = banked
        if i == 0:
            m.kind = "opened"
        elif open_after == 0:
            m.kind = "closed"
        elif opens and closes:
            m.kind = "adjusted"
        elif opens:
            m.kind = "added"
        else:
            m.kind = "reduced"
    return moments


def closed_at(basket: Basket) -> datetime | None:
    """When the last leg came off, if they all have."""
    if not basket.legs or any(leg.is_open for leg in basket.legs):
        return None
    return max(leg.exit_at for leg in basket.legs if leg.exit_at is not None)


def realized(basket: Basket) -> float:
    return sum(
        _signed(leg.side) * (leg.exit_price - leg.entry_price) * leg.quantity
        for leg in basket.legs
        if leg.exit_price is not None
    )

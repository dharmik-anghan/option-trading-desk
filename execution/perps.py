"""Orders on the perpetuals desk: check, record, send, note what happened.

The order of those verbs is the point. Checks run here, server-side, so no
client can skip them, and the attempt is written to the log *before* the
request leaves - so a process that dies mid-send still leaves a record that
something was tried. This is the only code that places, closes or protects a
perpetuals position.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from broker.base import Broker, PerpetualsData
from broker.errors import BrokerError
from broker.models import OrderRequest
from broker.perp_models import PerpPosition
from risk.perps import PerpLimits, PerpOrderCheck, check_perp_order
from settings import Settings
from storage.perp_order_repo import note_outcome, record_order

log = logging.getLogger(__name__)


def perp_limits(settings: Settings) -> PerpLimits:
    """The per-order caps the settings set for this desk."""
    return PerpLimits(
        max_quantity=settings.shark_max_quantity,
        max_notional=settings.shark_max_notional,
        max_leverage=settings.shark_max_leverage,
    )


@dataclass(frozen=True)
class PerpOrder:
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"]
    quantity: float
    leverage: float
    margin_mode: Literal["ISOLATED", "CROSS"]
    #: Required for a limit order, ignored for a market one.
    limit_price: float | None = None


@dataclass(frozen=True)
class Attempt:
    """What became of an order: sent or not, and why, in the venue's words when
    it was the venue that decided."""

    sent: bool
    outcome: str
    venue_order_id: str | None
    #: The row in the order log, so an attempt can be looked up afterwards.
    record_id: int


@dataclass(frozen=True)
class Placed:
    attempt: Attempt
    checks: PerpOrderCheck


def place(
    conn: sqlite3.Connection,
    broker: Broker,
    order: PerpOrder,
    *,
    price: float,
    limits: PerpLimits,
    now: datetime,
) -> Placed:
    """Check an order against the caps and the venue's rules, record it, send it.

    `price` is what it is sized against - the streamed price for a market order.
    This sends real orders: the caps in `risk/perps.py` are what stands between a
    mistake in this program and a position.
    """
    # The venue's own rules for this contract: its leverage ceiling, and the
    # smallest order it will accept at this price. Asked rather than remembered -
    # they differ per contract and the size floor moves with the price, because it
    # is a notional minimum rather than a quantity one.
    venue_max_leverage = 0.0
    smallest = 0.0
    if isinstance(broker, PerpetualsData):
        try:
            contract = broker.get_contracts().get(order.symbol)
            if contract is not None:
                venue_max_leverage = contract.max_leverage
                smallest = contract.smallest_order(price)
        except BrokerError:
            # Not fatal: without the catalogue the venue's own limits go
            # unchecked, and it will reject the order itself if they are broken.
            log.warning("could not read contract limits for %s", order.symbol)

    checks = check_perp_order(
        order.quantity,
        price,
        order.leverage,
        limits,
        venue_max_leverage=venue_max_leverage,
        smallest_order=smallest,
    )
    refused = None if checks.passed else "; ".join(checks.reasons)
    reason = refused or "Sending"

    record_id = record_order(
        conn,
        at=now.isoformat(),
        symbol=order.symbol,
        side=order.side,
        order_type=order.order_type,
        quantity=order.quantity,
        price=price,
        leverage=order.leverage,
        notional=checks.notional,
        sent=False,
        reason=reason,
    )

    sent = False
    venue_order_id: str | None = None
    if checks.passed:
        try:
            # Before the order, and the order is abandoned if it fails. The venue
            # has no leverage or margin-mode field on an order and applies whatever
            # the symbol was last set to, so skipping this does not mean
            # "defaults" - it means whatever the account happens to hold, which on
            # this one was the maximum of 150x against a chosen 10x.
            if isinstance(broker, PerpetualsData):
                broker.set_preference(order.symbol, order.leverage, order.margin_mode)
            result = broker.place_order(
                OrderRequest(
                    symbol=order.symbol,
                    quantity=order.quantity,
                    side=order.side,
                    order_type=order.order_type,
                    limit_price=order.limit_price or 0.0,
                )
            )
            sent = True
            venue_order_id = result.order_id
            reason = result.message or "Accepted"
        except BrokerError as exc:
            # Covers both steps. If the leverage did not take, nothing is placed:
            # an order at 150x when 10x was asked for is worse than no order.
            reason = f"Venue refused it: {exc.message}"
        note_outcome(conn, record_id, sent=sent, reason=reason, venue_order_id=venue_order_id)

    return Placed(Attempt(sent, reason, venue_order_id, record_id), checks)


class PositionGone(LookupError):
    """The position is no longer open - a stop fired, or it was closed elsewhere."""


def close(
    conn: sqlite3.Connection, broker: PerpetualsData, position_id: str, *, now: datetime
) -> Attempt:
    """Close one position at the market, for its full size.

    The size and side come from the venue's own view of the position, read now
    rather than taken from a caller - a stale quantity would leave a remainder
    open. The order is reduce-only regardless, so the worst outcome of a race is
    that nothing happens. No risk checks: every one of them exists to stop a
    position being opened by mistake, and none should stop one being closed.

    Raises `PositionGone`, or `BrokerError` when the venue refuses.
    """
    position = _open_position(broker, position_id)
    record_id = record_order(
        conn,
        at=now.isoformat(),
        symbol=position.symbol,
        side="SELL" if position.is_long else "BUY",
        order_type="MARKET",
        quantity=position.quantity,
        price=position.mark_price,
        leverage=position.leverage,
        notional=position.quantity * (position.mark_price or position.entry_price),
        sent=False,
        reason="Closing",
    )
    try:
        result = broker.close_position(position)
    except BrokerError as exc:
        note_outcome(
            conn, record_id, sent=False, reason=f"Venue refused it: {exc.message}",
            venue_order_id=None,
        )
        raise
    note_outcome(
        conn, record_id, sent=True, reason=result.message or "closed",
        venue_order_id=result.order_id,
    )
    return Attempt(True, result.message or "closed", result.order_id or None, record_id)


def _open_position(broker: PerpetualsData, position_id: str) -> PerpPosition:
    position = next(
        (p for p in broker.get_perp_positions() if p.position_id == position_id), None
    )
    if position is None:
        raise PositionGone(position_id)
    return position


def protect(
    broker: PerpetualsData,
    position_id: str,
    *,
    quantity: float,
    take_profit: float | None,
    stop_loss: float | None,
) -> None:
    """Have the venue hold a take-profit and stop-loss against a position.

    The one write on this desk that can only reduce risk: both legs are
    reduce-only, so the worst outcome of a mistake is a position closed earlier
    than intended. A `BrokerError` is never swallowed - protection that silently
    failed to attach is worse than none, because you would believe it was there.
    """
    broker.set_protection(
        position_id, quantity=quantity, take_profit=take_profit, stop_loss=stop_loss
    )

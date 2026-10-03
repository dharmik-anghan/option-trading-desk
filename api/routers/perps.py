"""The perpetuals desk: live prices, candles and positions from Shark.

A separate router from `market`, not a venue parameter on it, because the two
desks have almost no vocabulary in common. An option chain means nothing here,
and leverage, funding and a liquidation price mean nothing there. Sharing the
endpoints would mean half the fields being null on either side.

Prices come from the tick stream rather than a request per read: this venue allows
60 requests a minute, and it pushes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from api.deps import DbPathDep, PerpsBrokerDep, PerpsVenueDep
from api.store import open_db
from broker.base import PerpetualsData
from broker.errors import BrokerError
from broker.perp_models import ContractSpec
from execution.perps import PerpOrder, PositionGone, close, perp_limits, place, protect
from settings import load_settings
from storage.perp_order_repo import recent_orders
from streaming import TickHub
from streaming.sse import sse, tick_events
from venues import AssetClass, Capability, for_venue, serving
from venues.calendar import is_open
from venues.instruments import instrument, listed_on

log = logging.getLogger(__name__)

router = APIRouter(tags=["perps"], prefix="/api/perps")


class InstrumentResponse(BaseModel):
    symbol: str
    name: str
    quote_asset: str
    #: The venue's own ceiling for this contract. 150x on BTCUSDT, 75x on gold,
    #: 50x on oil - published by the venue rather than written down here, since a
    #: copy of somebody else's rule goes stale silently.
    max_leverage: float
    #: Smallest order the venue will accept at the current price. Usually set by a
    #: notional floor rather than a quantity one, so it moves with the price:
    #: BTCUSDT allows 0.001 but demands 115 USDT, which at 84,000 is 0.002.
    min_quantity: float
    min_notional: float
    price_dp: int
    quantity_dp: int
    #: Whether this instrument is trading right now. Per instrument, not per
    #: venue: BTCUSDT never closes while gold and oil stand down at the weekend.
    open: bool


class PriceResponse(BaseModel):
    symbol: str
    #: None when the stream has not carried this symbol yet - not zero, which
    #: would be a market at nothing.
    price: float | None
    #: Seconds since this price arrived, by our clock, so a dead stream shows as
    #: a stale price rather than a current one.
    age_seconds: float | None
    #: The venue's own 24-hour change, as a percentage. Its figure, not ours: a
    #: market with no close has no yesterday to measure against.
    change_pct: float | None


class StreamStatus(BaseModel):
    connected: bool
    ticks: int
    #: Ticks dropped because a reader fell behind. Reported rather than hidden:
    #: a number climbing here means something is not keeping up.
    dropped: int
    subscribers: int


class PositionResponse(BaseModel):
    symbol: str
    name: str
    side: str
    quantity: float
    entry_price: float
    #: What it is worth now, from the stream if the venue gave no mark.
    price: float | None
    leverage: float
    margin_type: str
    #: Margin posted, in the quote currency.
    margin: float
    #: The same, in the margin currency - what the account is actually debited.
    #: The venue reports both and they differ by the conversion rate, so one
    #: labelled as the other is out by a factor of a hundred.
    margin_in_margin_asset: float | None
    #: Quote currency per unit of margin currency, so the desk can work profit
    #: out live from a streamed price and still show it in the account's money.
    conversion_rate: float | None
    #: In the quote asset. Taken from the venue when it reports one, worked out
    #: from the price when it does not - and `pnl_is_ours` says which, because a
    #: figure we derived should not be presented as the venue's.
    unrealized_pnl: float | None
    pnl_is_ours: bool
    liquidation_price: float | None
    #: Distance to liquidation as a fraction of price. The number that matters on
    #: a leveraged position, and comparable across instruments in a way that a
    #: difference in points is not.
    liquidation_distance: float | None
    position_id: str
    #: Whether the venue is holding a stop for this position. The thing worth
    #: seeing first on a leveraged book: an exchange-held stop works with this app
    #: closed, and its absence means nothing closes the position but the market.
    protected: bool
    take_profit_orders: int
    stop_loss_orders: int


class DeskResponse(BaseModel):
    venue: str
    name: str
    #: Prices are in this.
    quote_currency: str
    #: Balances and P&L are in this, which is not the same thing on this venue.
    money_currency: str
    instruments: list[InstrumentResponse]
    prices: list[PriceResponse]
    positions: list[PositionResponse]
    #: Why the position list is empty, when it is empty because of a failure
    #: rather than because nothing is open. The two look identical otherwise, and
    #: "no positions" is a dangerous thing to show wrongly.
    positions_error: str | None
    stream: StreamStatus


def _hub(request: Request) -> TickHub | None:
    """The process-wide hub, or None before the lifespan has run (as in tests)."""
    hub = getattr(request.app.state, "tick_hub", None)
    return hub if isinstance(hub, TickHub) else None


@router.get("", response_model=DeskResponse)
def desk(request: Request, spec: PerpsVenueDep, broker: PerpsBrokerDep) -> DeskResponse:
    """Everything the perpetuals desk needs to draw itself once."""
    hub = _hub(request)
    stream = getattr(request.app.state, "tick_streams", {}).get(spec.id)
    now = datetime.now(UTC)

    # The venue's published limits, if it will tell us. Without them the ticket
    # falls back to what the instrument carries and the venue does its own
    # rejecting, which is worse but not broken.
    specs: dict[str, ContractSpec] = {}
    if isinstance(broker, PerpetualsData):
        try:
            specs = broker.get_contracts()
        except BrokerError:
            log.warning("could not read contract limits")

    instruments = []
    for i in for_venue(spec.id):
        contract = specs.get(i.symbol)
        streamed = hub.price(i.symbol) if hub is not None else None
        instruments.append(
            InstrumentResponse(
                symbol=i.symbol,
                name=i.name,
                quote_asset=i.quote_asset,
                max_leverage=contract.max_leverage if contract else 0.0,
                min_quantity=(
                    contract.smallest_order(streamed)
                    if contract is not None and streamed is not None
                    else (contract.min_quantity if contract else 0.0)
                ),
                min_notional=contract.min_notional if contract else 0.0,
                price_dp=contract.price_dp if contract else i.price_dp,
                quantity_dp=contract.quantity_dp if contract else i.quantity_dp,
                open=is_open(i.session, now),
            )
        )
    prices = []
    for i in for_venue(spec.id):
        tick = hub.tick(i.symbol) if hub is not None else None
        prices.append(
            PriceResponse(
                symbol=i.symbol,
                price=tick.price if tick is not None else None,
                age_seconds=hub.age_seconds(i.symbol) if hub is not None else None,
                change_pct=tick.change_pct if tick is not None else None,
            )
        )
    positions: list[PositionResponse] = []
    positions_error: str | None = None
    if isinstance(broker, PerpetualsData):
        try:
            for p in broker.get_perp_positions():
                listed = instrument(p.symbol)
                price = p.mark_price or (hub.price(p.symbol) if hub is not None else None)
                pnl = p.unrealized_pnl
                ours = False
                if pnl is None and price is not None:
                    # The venue did not report one. Worked out here rather than
                    # shown as zero, and flagged as ours so the screen can say so.
                    direction = 1 if p.is_long else -1
                    pnl = direction * (price - p.entry_price) * p.quantity
                    ours = True
                positions.append(
                    PositionResponse(
                        symbol=p.symbol,
                        name=listed.name if listed else p.symbol,
                        side=p.side,
                        quantity=p.quantity,
                        entry_price=p.entry_price,
                        price=price,
                        leverage=p.leverage,
                        margin_type=p.margin_type,
                        margin=p.margin,
                        margin_in_margin_asset=p.margin_in_margin_asset,
                        conversion_rate=p.conversion_rate,
                        unrealized_pnl=pnl,
                        pnl_is_ours=ours,
                        liquidation_price=p.liquidation_price,
                        liquidation_distance=p.liquidation_distance(price),
                        position_id=p.position_id,
                        protected=p.is_protected,
                        take_profit_orders=p.take_profit_orders,
                        stop_loss_orders=p.stop_loss_orders,
                    )
                )
        except BrokerError as exc:
            # Said out loud rather than swallowed: an empty list because the
            # request failed looks exactly like an empty list because nothing is
            # open, and on a leveraged book those are very different.
            positions_error = exc.message

    return DeskResponse(
        venue=spec.id,
        name=spec.name,
        quote_currency=spec.quote_currency,
        money_currency=spec.money_currency,
        instruments=instruments,
        prices=prices,
        positions=positions,
        positions_error=positions_error,
        stream=StreamStatus(
            connected=bool(getattr(stream, "connected", False)),
            ticks=int(getattr(hub, "received", 0) or 0),
            dropped=int(getattr(hub, "dropped", 0) or 0),
            subscribers=int(getattr(hub, "subscriber_count", 0) or 0),
        ),
    )


class ProtectionRequest(BaseModel):
    """Levels for the venue to hold. Prices, not distances.

    At least one is required, and each is sent only when given - so setting a
    stop does not clear a target by omission, which would remove protection while
    appearing to add it.
    """

    quantity: float = Field(gt=0)
    take_profit: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _at_least_one(self) -> ProtectionRequest:
        if self.take_profit is None and self.stop_loss is None:
            raise ValueError("give a take-profit, a stop, or both")
        return self


@router.post("/positions/{position_id}/protection", status_code=204)
def set_protection(
    position_id: str, body: ProtectionRequest, spec: PerpsVenueDep, broker: PerpsBrokerDep
) -> None:
    """Have the venue hold a take-profit and stop-loss against a position.

    The levels are held by the exchange, which is the point - they fire with this
    app closed and the machine asleep, on a market that trades overnight. See
    `execution.perps.protect`.
    """
    if not spec.can(Capability.PERPETUALS) or not isinstance(broker, PerpetualsData):
        raise HTTPException(status_code=501, detail="this venue cannot hold a stop")
    try:
        protect(
            broker,
            position_id,
            quantity=body.quantity,
            take_profit=body.take_profit,
            stop_loss=body.stop_loss,
        )
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc


class OrderRequest(BaseModel):
    """An order on this desk.

    Leverage is required rather than defaulted: a default would be a decision
    about risk taken quietly, and on a venue offering 150x the difference between
    8 and 80 is the difference between a position and a lottery ticket.
    """

    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    quantity: float = Field(gt=0)
    leverage: float = Field(gt=0)
    #: ISOLATED risks only the margin behind this position; CROSS puts the rest of
    #: the account behind it. Defaulted to the safer one rather than inherited,
    #: because inheriting it is how an order ends up on settings nobody chose.
    margin_mode: Literal["ISOLATED", "CROSS"] = "ISOLATED"
    #: Required for a limit order, ignored for a market one.
    limit_price: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _limit_needs_a_price(self) -> OrderRequest:
        if self.order_type == "LIMIT" and self.limit_price is None:
            raise ValueError("a limit order needs a price")
        return self


class CheckResponse(BaseModel):
    passed: bool
    reason: str


class OrderResponse(BaseModel):
    #: Whether it actually left. False means it was refused, and `reasons` says
    #: why - there is no third state now that nothing is held back.
    sent: bool
    checks: list[CheckResponse]
    reasons: list[str]
    #: What happened, in the venue's words when it was the venue that decided.
    #: Without this the screen could only say "not sent" and leave you guessing,
    #: which is exactly what it did.
    outcome: str
    notional: float
    #: What it was priced against, which for a market order is the streamed price.
    price: float | None
    venue_order_id: str | None
    #: The row in the order log, so an attempt can be looked up afterwards.
    record_id: int


@router.post("/orders", response_model=OrderResponse)
def place_order(
    request: Request, body: OrderRequest, db_path: DbPathDep, broker: PerpsBrokerDep
) -> OrderResponse:
    """Check an order, record it, and send it - see `execution.perps.place`.

    Sizing is against the streamed price for a market order. A market order with
    no price to size against is refused rather than sent blind - the notional cap
    cannot be applied without one, and an uncapped market order is the thing the
    caps exist to prevent.
    """
    if instrument(body.symbol) is None:
        raise HTTPException(status_code=404, detail=f"{body.symbol} is not on this desk")

    hub = _hub(request)
    streamed = hub.price(body.symbol) if hub is not None else None
    price = body.limit_price if body.order_type == "LIMIT" else streamed
    if price is None:
        raise HTTPException(
            status_code=503,
            detail="No price for this instrument yet, so the order cannot be sized or capped",
        )

    order = PerpOrder(
        symbol=body.symbol,
        side=body.side,
        order_type=body.order_type,
        quantity=body.quantity,
        leverage=body.leverage,
        margin_mode=body.margin_mode,
        limit_price=body.limit_price,
    )
    conn = open_db(db_path)
    try:
        placed = place(
            conn,
            broker,
            order,
            price=price,
            limits=perp_limits(load_settings()),
            now=datetime.now(UTC),
        )
    finally:
        conn.close()

    checks = placed.checks
    return OrderResponse(
        sent=placed.attempt.sent,
        outcome=placed.attempt.outcome,
        checks=[CheckResponse(passed=c.passed, reason=c.reason) for c in checks.checks],
        reasons=checks.reasons,
        notional=checks.notional,
        price=price,
        venue_order_id=placed.attempt.venue_order_id,
        record_id=placed.attempt.record_id,
    )


class OrderRecordResponse(BaseModel):
    id: int
    at: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: float | None
    leverage: float
    notional: float
    sent: bool
    reason: str
    venue_order_id: str | None


@router.get("/orders", response_model=list[OrderRecordResponse])
def order_log(db_path: DbPathDep, limit: int = 50) -> list[OrderRecordResponse]:
    """Every order this program formed, newest first - refusals and rehearsals too."""
    conn = open_db(db_path)
    try:
        return [
            OrderRecordResponse(
                id=r.id,
                at=r.at,
                symbol=r.symbol,
                side=r.side,
                order_type=r.order_type,
                quantity=r.quantity,
                price=r.price,
                leverage=r.leverage,
                notional=r.notional,
                sent=r.sent,
                reason=r.reason,
                venue_order_id=r.venue_order_id,
            )
            for r in recent_orders(conn, limit)
        ]
    finally:
        conn.close()


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    """Prices as they arrive, rather than the browser asking every two seconds.

    The socket to the venue was already here; this is the missing half. A tick that
    arrived from the exchange reached the hub and then sat there until the page
    asked for it, which is why /api/perps was being called every two seconds.

    Only this desk's instruments: the hub also carries the options venue's ticks.
    """
    # The app's shutdown flag, when there is one. Without it the generator loops
    # until its reader disconnects, and on shutdown the reader has not
    # disconnected - so uvicorn waits for the response and the response waits for
    # the reader.
    closing: asyncio.Event | None = getattr(request.app.state, "shutting_down", None)
    symbols = listed_on(serving(AssetClass.PERPETUALS).id)
    return sse(tick_events(request, _hub(request), closing, symbols))


class CloseResponse(BaseModel):
    closed: bool
    #: What happened, in the venue's words when it decided.
    outcome: str
    venue_order_id: str | None
    record_id: int


@router.post("/positions/{position_id}/close", response_model=CloseResponse)
def close_position(position_id: str, db_path: DbPathDep, broker: PerpsBrokerDep) -> CloseResponse:
    """Close one position at the market, for its full size - see `execution.perps.close`.

    The desk could open a position and not close one, which is the wrong way round:
    if something has gone wrong, this should be where you get out rather than where
    you watch it happen.
    """
    if not isinstance(broker, PerpetualsData):
        raise HTTPException(status_code=501, detail="this venue holds no positions")
    conn = open_db(db_path)
    try:
        attempt = close(conn, broker, position_id, now=datetime.now(UTC))
    except PositionGone:
        # Already gone, by a stop firing or a close elsewhere. Not an error worth
        # alarming anyone with, but not a success either.
        raise HTTPException(status_code=404, detail="That position is no longer open") from None
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    finally:
        conn.close()

    return CloseResponse(
        closed=True,
        outcome=attempt.outcome,
        venue_order_id=attempt.venue_order_id,
        record_id=attempt.record_id,
    )

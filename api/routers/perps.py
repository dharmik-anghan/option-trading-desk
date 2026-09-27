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
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from api.dependencies import broker_for
from api.deps import DbPathDep
from api.store import open_db
from broker.base import PerpetualsData
from broker.errors import BrokerError
from broker.models import OrderRequest as BrokerOrderRequest
from broker.models import Tick
from broker.shark.models import ContractSpec
from risk.perps import check_perp_order
from settings import load_settings
from storage.perp_order_repo import note_outcome, recent_orders, record_order
from streaming import TickHub
from venues import Capability, for_venue
from venues import get as get_venue
from venues.calendar import is_open
from venues.instruments import instrument

log = logging.getLogger(__name__)

router = APIRouter(tags=["perps"], prefix="/api/perps")

VENUE_ID = "shark"


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
def desk(request: Request) -> DeskResponse:
    """Everything the perpetuals desk needs to draw itself once."""
    spec = get_venue(VENUE_ID)
    hub = _hub(request)
    stream = getattr(request.app.state, "tick_stream", None)
    now = datetime.now(UTC)

    # The venue's published limits, if it will tell us. Without them the ticket
    # falls back to what the instrument carries and the venue does its own
    # rejecting, which is worse but not broken.
    specs: dict[str, ContractSpec] = {}
    broker = broker_for(spec)
    if isinstance(broker, PerpetualsData):
        try:
            specs = broker.get_contracts()
        except BrokerError:
            log.warning("could not read contract limits")

    instruments = []
    for i in for_venue(VENUE_ID):
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
    for i in for_venue(VENUE_ID):
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
def set_protection(position_id: str, body: ProtectionRequest) -> None:
    """Have the venue hold a take-profit and stop-loss against a position.

    A write against a live account, and the one write on this desk that can only
    reduce risk: both legs are reduce-only by construction, so the worst outcome
    of a mistake here is a position closed earlier than intended.

    The levels are held by the exchange, which is the point - they fire with this
    app closed and the machine asleep, on a market that trades overnight.
    """
    spec = get_venue(VENUE_ID)
    if not spec.can(Capability.PERPETUALS):
        raise HTTPException(status_code=501, detail="this venue holds no positions")
    broker = broker_for(spec)
    if not isinstance(broker, PerpetualsData):
        raise HTTPException(status_code=501, detail="this venue cannot hold a stop")
    try:
        broker.set_protection(
            position_id,
            quantity=body.quantity,
            take_profit=body.take_profit,
            stop_loss=body.stop_loss,
        )
    except BrokerError as exc:
        # Never swallowed: protection that silently failed to attach is worse than
        # none, because you would believe it was there.
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
def place_order(request: Request, body: OrderRequest, db_path: DbPathDep) -> OrderResponse:
    """Check an order, record it, and send it.

    The order of those verbs is the point. Checks run server-side, so a client
    cannot skip them, and the attempt is written to the log *before* the request
    leaves - so a process that dies mid-send still leaves a record that something
    was tried.

    This sends real orders. The caps in `risk/perps.py` are what stands between a
    mistake in this program and a position, which is why they are checked here
    rather than in the browser.

    Sizing is against the streamed price for a market order. A market order with
    no price to size against is refused rather than sent blind - the notional cap
    cannot be applied without one, and an uncapped market order is the thing the
    caps exist to prevent.
    """
    spec = get_venue(VENUE_ID)
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

    limits = load_settings().perp_limits
    broker = broker_for(spec)

    # The venue's own rules for this contract: its leverage ceiling, and the
    # smallest order it will accept at this price. Asked rather than remembered -
    # they differ per contract and the size floor moves with the price, because it
    # is a notional minimum rather than a quantity one.
    venue_max_leverage = 0.0
    smallest = 0.0
    if isinstance(broker, PerpetualsData):
        try:
            contract = broker.get_contracts().get(body.symbol)
            if contract is not None:
                venue_max_leverage = contract.max_leverage
                smallest = contract.smallest_order(price)
        except BrokerError:
            # Not fatal: without the catalogue the venue's own limits go
            # unchecked, and it will reject the order itself if they are broken.
            log.warning("could not read contract limits for %s", body.symbol)

    outcome = check_perp_order(
        body.quantity,
        price,
        body.leverage,
        limits,
        venue_max_leverage=venue_max_leverage,
        smallest_order=smallest,
    )

    now = datetime.now(UTC).isoformat()
    refused = None if outcome.passed else "; ".join(outcome.reasons)
    reason = refused or "Sending"

    conn = open_db(db_path)
    try:
        record_id = record_order(
            conn,
            at=now,
            symbol=body.symbol,
            side=body.side,
            order_type=body.order_type,
            quantity=body.quantity,
            price=price,
            leverage=body.leverage,
            notional=outcome.notional,
            sent=False,
            reason=reason,
        )

        sent = False
        venue_order_id: str | None = None
        if outcome.passed:
            try:
                # Before the order, and the order is abandoned if it fails. The
                # venue has no leverage or margin-mode field on an order and applies
                # whatever the symbol was last set to, so skipping this does not
                # mean "defaults" - it means whatever the account happens to hold,
                # which on this one was the maximum of 150x against a chosen 10x.
                if isinstance(broker, PerpetualsData):
                    # Leverage and margin mode together: an order carries neither,
                    # so both would otherwise be whatever the symbol was last set
                    # to. Leverage was already this bug once.
                    broker.set_preference(body.symbol, body.leverage, body.margin_mode)
                result = broker.place_order(
                    BrokerOrderRequest(
                        symbol=body.symbol,
                        quantity=body.quantity,
                        side=body.side,
                        order_type=body.order_type,
                        limit_price=body.limit_price or 0.0,
                    )
                )
                sent = True
                venue_order_id = result.order_id
                reason = result.message or "Accepted"
            except BrokerError as exc:
                # Covers both steps. If the leverage did not take, nothing is
                # placed: an order at 150x when 10x was asked for is worse than no
                # order at all.
                reason = f"Venue refused it: {exc.message}"
            note_outcome(
                conn, record_id, sent=sent, reason=reason, venue_order_id=venue_order_id
            )
    finally:
        conn.close()

    return OrderResponse(
        sent=sent,
        outcome=reason,
        checks=[CheckResponse(passed=c.passed, reason=c.reason) for c in outcome.checks],
        reasons=outcome.reasons,
        notional=outcome.notional,
        price=price,
        venue_order_id=venue_order_id,
        record_id=record_id,
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


#: How long to wait for a tick before sending a keep-alive. Proxies and browsers
#: drop a connection that has said nothing, and a silent instrument is normal at
#: three in the morning.
STREAM_HEARTBEAT = 15.0


async def _next_tick(
    queue: asyncio.Queue[Tick], closing: asyncio.Event | None
) -> Tick | None:
    """The next tick, or None if the app is going down first.

    Raises TimeoutError when neither happens within the heartbeat, which is the
    normal case for an instrument nobody is trading at three in the morning.
    """
    if closing is None:
        return await asyncio.wait_for(queue.get(), timeout=STREAM_HEARTBEAT)

    waits = [asyncio.create_task(queue.get()), asyncio.create_task(closing.wait())]
    try:
        done, _ = await asyncio.wait(
            waits, timeout=STREAM_HEARTBEAT, return_when=asyncio.FIRST_COMPLETED
        )
        if not done:
            raise TimeoutError
        for task in waits:
            if task.done() and not task.cancelled():
                result = task.result()
                if isinstance(result, Tick):
                    return result
        return None
    finally:
        # A tick pulled from the queue by a task nobody read is a lost tick, but
        # this only happens on shutdown or a heartbeat, where losing one is fine.
        for task in waits:
            task.cancel()


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    """Prices as they arrive, rather than the browser asking every two seconds.

    Server-sent events, not a websocket. The traffic is one-way - the desk needs
    prices pushed and has nothing to say back - and SSE is a plain HTTP response a
    browser reconnects by itself, where a websocket would mean a protocol upgrade,
    a ping loop and reconnection logic of our own for the same result.

    The socket to the venue was already here; this is the missing half. A tick that
    arrived from the exchange reached the hub and then sat there until the page
    asked for it, which is why /api/perps was being called every two seconds.

    Each reader gets its own bounded queue, and the queue is unregistered when the
    reader goes - a browser closing a tab must not leave one growing behind it.
    """

    async def events() -> AsyncIterator[str]:
        hub = _hub(request)
        # The app's shutdown flag, when there is one. Without it this generator
        # loops until its reader disconnects, and on shutdown the reader has not
        # disconnected - so uvicorn waits for the response and the response waits
        # for the reader.
        closing: asyncio.Event | None = getattr(request.app.state, "shutting_down", None)
        if hub is None:
            # Said once, rather than holding a connection open that will never
            # carry anything: the stream never started.
            yield 'event: closed\ndata: {"reason":"no price stream"}\n\n'
            return
        with hub.subscribe() as queue:
            # The current picture first, so a page that has just loaded is not
            # blank until something moves.
            for symbol, price in hub.prices().items():
                yield f"data: {json.dumps({'symbol': symbol, 'price': price})}\n\n"
            while True:
                if await request.is_disconnected():
                    return
                if closing is not None and closing.is_set():
                    return
                try:
                    tick = await _next_tick(queue, closing)
                except TimeoutError:
                    # A comment, which SSE ignores: it keeps the connection open
                    # without the client having to filter a fake price.
                    yield ": keep-alive\n\n"
                    continue
                if tick is None:
                    # Shutting down.
                    return
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "symbol": tick.symbol,
                            "price": tick.price,
                            "change_pct": tick.change_pct,
                            "at": tick.at.isoformat(),
                        }
                    )
                    + "\n\n"
                )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            # Without this a proxy will buffer the stream and deliver it in lumps,
            # which defeats the point.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


class CloseResponse(BaseModel):
    closed: bool
    #: What happened, in the venue's words when it decided.
    outcome: str
    venue_order_id: str | None
    record_id: int


@router.post("/positions/{position_id}/close", response_model=CloseResponse)
def close_position(position_id: str, db_path: DbPathDep) -> CloseResponse:
    """Close one position at the market, for its full size.

    The desk could open a position and not close one, which is the wrong way round:
    if something has gone wrong, this should be where you get out rather than where
    you watch it happen.

    The size and side come from the venue's own view of the position, read now
    rather than sent by the client. A stale quantity from a page that has not
    refreshed would either leave a remainder open or - without reduce-only - open a
    position the other way. The order is reduce-only regardless, so the worst
    outcome of a race is that nothing happens.

    No risk checks. Every one of them exists to stop a position being opened by
    mistake, and none of them should be able to stop one being closed.
    """
    spec = get_venue(VENUE_ID)
    broker = broker_for(spec)
    if not isinstance(broker, PerpetualsData):
        raise HTTPException(status_code=501, detail="this venue holds no positions")

    try:
        position = next(
            (p for p in broker.get_perp_positions() if p.position_id == position_id), None
        )
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    if position is None:
        # Already gone, by a stop firing or a close elsewhere. Not an error worth
        # alarming anyone with, but not a success either.
        raise HTTPException(status_code=404, detail="That position is no longer open")

    conn = open_db(db_path)
    try:
        record_id = record_order(
            conn,
            at=datetime.now(UTC).isoformat(),
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
            raise HTTPException(status_code=502, detail=exc.message) from exc
        note_outcome(
            conn, record_id, sent=True, reason=result.message or "closed",
            venue_order_id=result.order_id,
        )
    finally:
        conn.close()

    return CloseResponse(
        closed=True,
        outcome=result.message or "closed",
        venue_order_id=result.order_id or None,
        record_id=record_id,
    )

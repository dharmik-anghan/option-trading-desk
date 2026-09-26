"""The perpetuals desk: live prices, candles and positions from Shark.

A separate router from `market`, not a venue parameter on it, because the two
desks have almost no vocabulary in common. An option chain means nothing here,
and leverage, funding and a liquidation price mean nothing there. Sharing the
endpoints would mean half the fields being null on either side.

Prices come from the tick stream rather than a request per read: this venue allows
60 requests a minute, and it pushes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.dependencies import broker_for
from broker.base import PerpetualsData
from broker.errors import BrokerError
from streaming import TickHub
from venues import Capability, for_venue
from venues import get as get_venue
from venues.calendar import is_open
from venues.instruments import instrument

router = APIRouter(tags=["perps"], prefix="/api/perps")

VENUE_ID = "shark"


class InstrumentResponse(BaseModel):
    symbol: str
    name: str
    quote_asset: str
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
    #: Margin posted, in `margin_currency` - not the currency the price is in.
    margin: float
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


class CandleResponse(BaseModel):
    at: str
    open: float
    high: float
    low: float
    close: float
    volume: float


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

    instruments = [
        InstrumentResponse(
            symbol=i.symbol,
            name=i.name,
            quote_asset=i.quote_asset,
            price_dp=i.price_dp,
            quantity_dp=i.quantity_dp,
            open=is_open(i.session, now),
        )
        for i in for_venue(VENUE_ID)
    ]
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
    broker = broker_for(spec)
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
                        unrealized_pnl=pnl,
                        pnl_is_ours=ours,
                        liquidation_price=p.liquidation_price,
                        liquidation_distance=p.liquidation_distance(price),
                        position_id=p.position_id,
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


@router.get("/candles/{symbol}", response_model=list[CandleResponse])
def candles(symbol: str, resolution: str = "60", days: int = 5) -> list[CandleResponse]:
    """Candles for one instrument.

    Polled rather than streamed: a chart is redrawn on a timeframe change, not on
    every tick, and the last candle is kept current from the tick stream on the
    client side.
    """
    if instrument(symbol) is None:
        raise HTTPException(status_code=404, detail=f"{symbol} is not on this desk")
    spec = get_venue(VENUE_ID)
    if not spec.can(Capability.HISTORY):
        raise HTTPException(status_code=501, detail="this venue serves no history")
    broker = broker_for(spec)
    today = date.today()
    try:
        rows = broker.get_history(symbol, resolution, today - timedelta(days=days), today)
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    return [
        CandleResponse(
            at=c.timestamp.isoformat(),
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
            volume=c.volume,
        )
        for c in rows
    ]

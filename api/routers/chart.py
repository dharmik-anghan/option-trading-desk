"""One chart endpoint, for every desk.

Both desks draw the same chart component; this is the other half of that - the
one place it gets bars from. `source` names whose candles they are, and that is
deliberately in the path rather than inferred: a perpetual on Shark, spot on
Binance and an NSE index are three different instruments that a chart must never
quietly substitute for one another.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request

from api.charting import CandlesResponse, days_for, drawn, series_for
from api.deps import bar_service
from broker.errors import BrokerError
from broker.factory import broker_for
from marketdata import Interval
from marketdata.models import Bar
from venues import Capability, for_venue
from venues import get as get_venue

router = APIRouter(tags=["chart"], prefix="/api/chart")


@router.get("/{source}/{symbol:path}", response_model=CandlesResponse)
def chart(
    request: Request,
    source: str,
    symbol: str,
    interval: str = "1h",
    days: int = 0,
    bars: int = 0,
    indicators: str = "",
) -> CandlesResponse:
    """Candles for one series, with indicators drawn over them.

    `indicators` names lines as "ema:20,ema:50,rsi:14" - or with a timeframe,
    "ema:50:4h". They are computed here rather than in the browser, through the
    same code a backtest reads them with, so the EMA on this chart and the EMA a
    rule would trade on are the same number.

    `bars` trims to the last N, which is how a panel asks for exactly the window
    something else is describing: the structure reading looks at a fixed bar
    count, and a chart showing a different one would be describing bars that are
    not on screen.

    `days` is how far back to read. Left out, it is worked out from `bars`, so a
    caller that thinks in bars - which is what a chart does - does not have to
    do the calendar arithmetic that differs at every size.
    """
    # A venue that publishes what it trades is asked whether it trades this. A
    # chart is only ever drawn for an instrument the desk knows; drawing one for
    # a symbol nobody listed means drawing a stranger's price with an order
    # ticket beside it. Sources with no list - a bare bar store, Binance - fall
    # through and answer from whatever they hold.
    listed = [i.symbol for i in for_venue(source)]
    if listed and symbol not in listed:
        raise HTTPException(status_code=404, detail=f"{symbol} is not on the {source} desk")
    try:
        size = Interval(interval)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{interval} is not a bar size") from None
    days = days or (days_for(size, bars) if bars else 5)

    service = bar_service(request)
    if service is None:
        # No store - a second copy of the app or a backfill is holding it. A desk
        # whose venue serves history can still draw from the venue directly,
        # which is better than a blank panel for however long the lock is held.
        return _straight_from(source, symbol, size, days, indicators, bars)

    held, note = series_for(service, source, symbol, size, days)
    if not held:
        raise HTTPException(status_code=404, detail=note)
    return drawn(source, note, held, size, indicators, keep=bars)


def _straight_from(
    source: str, symbol: str, size: Interval, days: int, indicators: str, keep: int
) -> CandlesResponse:
    """Ask the venue itself, for the minutes the store is locked."""
    try:
        spec = get_venue(source)
    except (KeyError, ValueError):
        spec = None
    if spec is None or not spec.can(Capability.HISTORY):
        raise HTTPException(
            status_code=503,
            detail=(
                "The bar store is open in another process, so no history can be read. "
                "It is retried every 30 seconds - a backfill script or a second "
                "copy of the app will be holding it."
            ),
        )
    broker = broker_for(spec)
    try:
        rows = broker.get_history(
            symbol, str(size), date.today() - timedelta(days=days), date.today()
        )
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    from_venue = [
        Bar(ts=c.timestamp, open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume)
        for c in rows
    ]
    note = "straight from the venue, the bar store is locked"
    return drawn(source, note, from_venue, size, indicators, keep=keep)

"""What history is held, and bars out of the store.

The first thing a backtest needs is not a strategy - it is knowing what data exists.
A run over a window the store does not cover is not a bad result, it is a
meaningless one, and the difference is invisible unless somebody says out loud which
series are held and how far back they go.

Deliberately venue-agnostic. The store keys on source, so this serves Shark's own
candles, Binance's and Yahoo's alike, and says which is which - because a backtest
of a Yahoo gold series is not a backtest of the perpetual that would have been
traded.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.deps import bar_service
from marketdata import BarService, Interval

router = APIRouter(tags=["bars"], prefix="/api/bars")


class SeriesResponse(BaseModel):
    source: str
    symbol: str
    interval: str
    bars: int
    #: The window actually held. A backtest asking for more than this is asking
    #: about time the store knows nothing of.
    first: str | None
    last: str | None


class BarResponse(BaseModel):
    at: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class BarsResponse(BaseModel):
    source: str
    symbol: str
    interval: str
    #: Why the series may be short or stale, when there is a reason worth saying.
    note: str
    bars: list[BarResponse]


def _service(request: Request) -> BarService:
    """The bar store, or a refusal that says why.

    Opened on demand and retried, not once at startup: a desk that came up
    beside a finishing backfill used to answer this for the rest of the day
    with the file unlocked the whole time.
    """
    service = bar_service(request)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "The bar store is open in another process, so no history can be read. "
                "It is retried every 30 seconds - a backfill script or a second "
                "copy of the app will be holding it."
            ),
        )
    return service


@router.get("/series", response_model=list[SeriesResponse])
def series(request: Request) -> list[SeriesResponse]:
    """Every series held, with how much of it and over what window."""
    service = _service(request)
    out: list[SeriesResponse] = []
    for held, count in service.held():
        span = service.span(held)
        out.append(
            SeriesResponse(
                source=held.source,
                symbol=held.symbol,
                interval=str(held.interval),
                bars=count,
                first=span[0].isoformat() if span else None,
                last=span[1].isoformat() if span else None,
            )
        )
    return out


@router.get("/{source}/{symbol}/{interval}", response_model=BarsResponse)
def bars(
    request: Request,
    source: str,
    symbol: str,
    interval: str,
    days: int = 365,
    refresh: bool = False,
) -> BarsResponse:
    """Bars for one series, from the store.

    `refresh` is off by default here, unlike a chart. A backtest reading the same
    window repeatedly should not be asking a source each time, and a run whose data
    changes underneath it is a run whose result cannot be reproduced.
    """
    try:
        size = Interval(interval)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{interval} is not a bar size") from None

    service = _service(request)
    result = service.stored(source, symbol, size, days=days, refresh=refresh)
    return BarsResponse(
        source=source,
        symbol=symbol,
        interval=str(size),
        note=result.note,
        bars=[
            BarResponse(
                at=b.ts.isoformat(),
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
            )
            for b in result.bars
        ],
    )

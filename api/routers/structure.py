"""What the price has been doing, at several sizes at once.

The answer is usually not the same at every size, and that disagreement is the
information rather than a fault in it. Run on NIFTY while this was written: the
fifteen-minute bars said uptrend, the hourly said downtrend, the daily said
downtrend and the weekly said uptrend. A panel showing one timeframe would have
reported whichever it happened to pick.

Every reading says how far behind it is. A swing cannot be recognised until `k`
bars have printed after it, so the most recent turn is a candidate rather than a
fact - and the candidate is sent separately from the settled ones so a screen
can draw it differently rather than pretending it is decided.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from analytics.structure import DEFAULT_K, Structure, read
from api.charting import days_for, series_for
from api.deps import bar_service
from marketdata import Interval
from marketdata.models import Bar
from venues import listed_on

router = APIRouter(tags=["structure"], prefix="/api/structure")

#: The sizes to read when a caller names none, longest first.
DEFAULT_SIZES = "1w,1d,4h,1h,15m"

#: How far back a reading looks, in bars, at every size.
#:
#: One number for both the label and the chart, which were two before: the
#: trend was read from the whole stored series - seven hundred daily bars - and
#: the chart drew the last hundred and eighty, so a reading could come from
#: swings nobody could see. What produced the label is now what is on screen.
#:
#: The same count means a different span at each size, which is the point: a
#: hundred and eighty weeks is three and a half years, a hundred and eighty
#: days is nine months, and a hundred and eighty fifteen-minute bars is about a
#: week. Each is a reasonable horizon for the size it belongs to.
LOOKBACK = 180


class SwingOut(BaseModel):
    at: str
    kind: str
    price: float
    confirmed: bool


class BreakOut(BaseModel):
    #: When the level was set, and when it was taken. A break is a span.
    from_at: str
    at: str
    price: float
    level: float
    continuation: bool


class FrameOut(BaseModel):
    interval: str
    #: Bars the reading was taken from, which is also what the chart shows.
    bars: int
    #: The span those bars cover, in words, so the horizon is not left implied.
    covers: str
    trend: str
    says: str
    high_label: str | None
    low_label: str | None
    swings: list[SwingOut]
    #: Every close through a swing level in the window, oldest first.
    breaks: list[BreakOut]
    #: Why this size has nothing, when it has nothing.
    note: str = ""


class StructureOut(BaseModel):
    #: Kept as `underlying` for the desk that has one; it is whatever symbol was
    #: read, on whichever venue.
    underlying: str
    source: str
    name: str
    k: int
    #: Bars each reading looks back over, the same at every size. The chart
    #: asks `/api/chart` for this many at whichever size is being looked at, so
    #: what is on screen is what the reading was taken from.
    lookback: int
    frames: list[FrameOut]
    #: Where every size agrees, if they do. The thing worth knowing at a glance.
    agreement: str
    caveats: list[str]


@router.get("/{source}/{symbol:path}", response_model=StructureOut)
def structure(
    request: Request,
    source: str,
    symbol: str,
    k: int = DEFAULT_K,
    sizes: str = DEFAULT_SIZES,
    bars: int = LOOKBACK,
) -> StructureOut:
    """Market structure at several sizes at once, for any series we hold.

    Not an options endpoint any more. Structure is a way of reading bars, and
    bars are bars - a perpetual makes higher highs and lower lows exactly as an
    index does. The desk that asks is what differs, and all it passes is which
    sizes it is offering and how many bars its chart is showing, so the reading
    describes the bars on screen rather than a window of its own choosing.
    """
    if not 1 <= k <= 20:
        raise HTTPException(status_code=400, detail="k has to be between 1 and 20")
    if not 20 <= bars <= 5000:
        raise HTTPException(status_code=400, detail="bars has to be between 20 and 5000")
    try:
        wanted = [Interval(piece.strip()) for piece in sizes.split(",") if piece.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{sizes} is not a list of bar sizes") from None
    if not wanted:
        raise HTTPException(status_code=400, detail="name at least one bar size")

    name = _name_of(source, symbol)
    if name is None:
        raise HTTPException(status_code=404, detail=f"{symbol} is not traded on {source}")

    service = bar_service(request)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="The bar store is open in another process, so no structure can be read",
        )

    frames: list[FrameOut] = []
    missing: list[str] = []
    for size in wanted:
        # Through the same helper the chart endpoint reads with, given the same
        # window. Two code paths to the same bars is how a panel ends up
        # describing swings that are not on the chart beside it.
        held, _note = series_for(
            service, source, symbol, size, days_for(size, bars), refresh=False
        )
        if not held:
            missing.append(str(size))
            frames.append(
                FrameOut(
                    interval=str(size),
                    bars=0,
                    covers="",
                    trend="unclear",
                    says="no bars stored at this size",
                    high_label=None,
                    low_label=None,
                    swings=[],
                    breaks=[],
                    note="Nothing is stored at this size, or under it to build it from",
                )
            )
            continue
        frames.append(_frame(size, held, k, bars))

    if all(f.bars == 0 for f in frames):
        raise HTTPException(
            status_code=404,
            detail=f"No bars are stored for {name} at any of {sizes}",
        )

    caveats: list[str] = []
    if missing:
        caveats.append(
            f"Nothing is stored at {', '.join(missing)}, so those sizes have no reading. "
            "Everything else here is from what has been backfilled"
        )

    trends = {f.trend for f in frames if f.bars and f.trend != "unclear"}
    agreement = (
        next(iter(trends)) if len(trends) == 1 else "the sizes disagree" if trends else "unclear"
    )

    return StructureOut(
        underlying=symbol,
        source=source,
        name=name,
        k=k,
        lookback=bars,
        frames=frames,
        agreement=agreement,
        caveats=caveats,
    )


def _name_of(source: str, symbol: str) -> str | None:
    """What this venue calls the symbol, or None if it does not trade it.

    The same guard the chart endpoint applies, and for the same reason: a
    reading of a symbol nobody listed is a reading of a stranger's price.
    """
    return listed_on(source).get(symbol)


def _covers(window: list[Bar]) -> str:
    """The window in words. "180 bars" means nothing without its size.

    From the first and last timestamps rather than from the bar count times the
    bar width. The two differ on anything intraday, and by a lot: a hundred and
    eighty fifteen-minute bars is forty-five hours of trading but about a week
    of calendar, because the market is shut for two thirds of the day and all
    of the weekend. The calendar answer is the one a reader means.
    """
    if len(window) < 2:
        return ""
    days = (window[-1].ts - window[0].ts).total_seconds() / 86400
    if days >= 365:
        return f"{days / 365:.1f} years"
    if days >= 60:
        return f"{days / 30:.0f} months"
    if days >= 45:
        return "about 6 weeks"
    if days >= 14:
        return f"{days / 7:.0f} weeks"
    if days >= 2:
        return f"{days:.0f} days"
    return f"{days * 24:.0f} hours"


def _frame(size: Interval, bars: list[Bar], k: int, lookback: int) -> FrameOut:
    # Sliced before reading, not after. The label and the chart have to come
    # from the same bars or the panel is describing something off screen.
    window = bars[-lookback:]
    found: Structure = read(window, k)
    return FrameOut(
        interval=str(size),
        bars=len(window),
        covers=_covers(window),
        trend=str(found.trend),
        says=found.says,
        high_label=found.high_label,
        low_label=found.low_label,
        swings=[
            SwingOut(
                at=s.at.isoformat(), kind=str(s.kind), price=s.price, confirmed=s.confirmed
            )
            for s in found.swings
        ],
        breaks=[
            BreakOut(
                from_at=b.from_at.isoformat(),
                at=b.at.isoformat(),
                price=b.price,
                level=b.level,
                continuation=b.continuation,
            )
            for b in found.breaks
        ],
    )

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
from venues import OPTION_UNDERLYINGS

router = APIRouter(tags=["structure"], prefix="/api/structure")

SOURCE = "fyers"

#: The sizes to read, longest first. Intraday needs stored intraday bars, which
#: is a separate backfill - a size with nothing behind it is reported as such
#: rather than silently missing.
SIZES: tuple[Interval, ...] = (
    Interval.W1,
    Interval.D1,
    Interval.H4,
    Interval.H1,
    Interval.M15,
)

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
    last_break: BreakOut | None
    #: Why this size has nothing, when it has nothing.
    note: str = ""


class StructureOut(BaseModel):
    underlying: str
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


@router.get("/{underlying:path}", response_model=StructureOut)
def structure(
    request: Request,
    underlying: str,
    k: int = DEFAULT_K,
) -> StructureOut:
    """Market structure across every size we hold bars for."""
    listed = dict(OPTION_UNDERLYINGS)
    if underlying not in listed:
        raise HTTPException(status_code=404, detail=f"{underlying} is not an underlying here")
    if not 1 <= k <= 20:
        raise HTTPException(status_code=400, detail="k has to be between 1 and 20")

    service = bar_service(request)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="The bar store is open in another process, so no structure can be read",
        )

    daily = service.stored(SOURCE, underlying, Interval.D1, days=365 * 4).bars
    intraday = service.stored(SOURCE, underlying, Interval.M15, days=200).bars

    caveats: list[str] = []
    if not daily:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No stored daily bars for {listed[underlying]}. Run scripts/backfill_nse.py"
            ),
        )
    if not intraday:
        caveats.append(
            "No intraday bars are stored, so the hourly and fifteen-minute readings are "
            "absent. Everything daily and above is from three years of stored history"
        )

    frames: list[FrameOut] = []
    for size in SIZES:
        # Through the same helper the chart endpoint reads with, given the same
        # window. Two code paths to the same bars is how a panel ends up
        # describing swings that are not on the chart beside it.
        bars, _note = series_for(
            service, SOURCE, underlying, size, days_for(size, LOOKBACK), refresh=False
        )
        if not bars:
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
                    last_break=None,
                    note="Needs intraday bars, which have not been backfilled",
                )
            )
            continue
        frames.append(_frame(size, bars, k))

    trends = {f.trend for f in frames if f.bars and f.trend != "unclear"}
    agreement = (
        next(iter(trends)) if len(trends) == 1 else "the sizes disagree" if trends else "unclear"
    )

    return StructureOut(
        underlying=underlying,
        name=listed[underlying],
        k=k,
        lookback=LOOKBACK,
        frames=frames,
        agreement=agreement,
        caveats=caveats,
    )


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


def _frame(size: Interval, bars: list[Bar], k: int) -> FrameOut:
    # Sliced before reading, not after. The label and the chart have to come
    # from the same bars or the panel is describing something off screen.
    window = bars[-LOOKBACK:]
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
        last_break=(
            BreakOut(
                from_at=found.last_break.from_at.isoformat(),
                at=found.last_break.at.isoformat(),
                price=found.last_break.price,
                level=found.last_break.level,
                continuation=found.last_break.continuation,
            )
            if found.last_break
            else None
        ),
    )

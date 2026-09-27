"""Candles for a chart, from one place.

There used to be two ways to get bars onto a chart. The perpetuals desk asked
`/api/perps/candles`, which read the venue's own series through the store and
computed whatever indicators were asked for. The options desk got its candles
folded into `/api/structure`, which read the stored daily series, resampled it
to whatever size was being looked at, and drew no indicators at all.

So the two desks' charts could not do the same things, and neither could gain
what the other had without the work being done twice. This module is the one
answer to "give me these bars, at this size, with these lines on them", and both
desks ask it.

The part that is not obvious: a source rarely holds every size. Fyers gives us
daily and fifteen-minute bars, and a four-hour chart is the fifteen-minute bars
combined rather than a fourth series. Resampling, not fetching, for the reason
`backtest/resample` gives - two fetched series can disagree about a boundary and
nothing on screen would ever show it.
"""

from __future__ import annotations

from pydantic import BaseModel

from backtest.lines import compute, parse_wanted
from backtest.resample import resample
from marketdata import BarService, Interval
from marketdata.models import Bar


class CandleResponse(BaseModel):
    at: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class LineResponse(BaseModel):
    """One indicator, aligned bar for bar with the candles."""

    label: str
    name: str
    length: int
    interval: str | None
    #: Whether it is a price and belongs on the price axis. An RSI runs 0-100
    #: and would be a flat line along the bottom of a chart scaled to Bitcoin.
    on_price: bool
    values: list[float | None]


class CandlesResponse(BaseModel):
    #: Whose candles these are. On a chart with an order ticket beside it, this is
    #: not decoration: a price from a source you are not trading is the wrong price.
    source: str
    #: Why the series may be short or stale, when there is a reason worth saying.
    note: str
    candles: list[CandleResponse]
    #: Whatever indicators were asked for, computed through the same code a
    #: backtest reads them with. Empty when none were.
    lines: list[LineResponse] = []


#: Sizes that can be combined into a longer one, smallest first. Only sizes a
#: backfill actually writes - there is no point reading a minute series nothing
#: stores.
BASES: tuple[Interval, ...] = (Interval.M1, Interval.M5, Interval.M15, Interval.H1, Interval.D1)


#: Calendar days to read per bar asked for, above and below a day.
#:
#: An exchange with a session prints a fraction of the bars the clock allows.
#: NSE trades six and a quarter hours of every twenty-four and five days of
#: every seven, so reading `bars x width` of calendar returns about a quarter of
#: what was asked for intraday and two thirds of it daily. Generous on purpose:
#: the extra is one more indexed range scan, and reading too little silently
#: shortens a chart in a way nobody would notice.
_SLACK_INTRADAY = 5.0
_SLACK_DAILY = 2.4


def days_for(interval: Interval, bars: int) -> int:
    """How far back to read so that `bars` bars of this size are in the window.

    One function because two would drift: the chart endpoint and the structure
    reading have to look at the same bars, and they only do that if they ask for
    the same window.
    """
    slack = _SLACK_INTRADAY if interval.seconds < Interval.D1.seconds else _SLACK_DAILY
    return max(2, int(bars * interval.seconds / 86400 * slack) + 2)


def series_for(
    service: BarService,
    source: str,
    symbol: str,
    interval: Interval,
    days: int,
    *,
    refresh: bool = True,
) -> tuple[list[Bar], str]:
    """Bars at one size, and a note about where they came from.

    Three ways, in order of preference:

    1. Through the source itself, when it is registered and lists this symbol.
       That is what a venue chart wants - the instrument an order would be in -
       and it stores what it fetches, so the history grows.
    2. Straight off disk at that exact size, for a series a backfill writes.
    3. Off disk at the largest smaller size held, combined up.

    The third is how the options desk draws anything but a day and a week: NSE
    bars are backfilled daily and at fifteen minutes, and every other size is
    arithmetic on those.
    """
    if refresh:
        fetched = service.bars(symbol, interval, days, source=source)
        if fetched.bars:
            return fetched.bars, fetched.note

    held = service.stored(source, symbol, interval, days=days)
    if held.bars:
        return held.bars, ""

    for base in sorted(BASES, key=lambda b: b.seconds, reverse=True):
        if base.seconds >= interval.seconds:
            continue
        under = service.stored(source, symbol, base, days=days)
        if under.bars:
            return resample(under.bars, interval), f"built from {base} bars"

    return [], f"Nothing stored for {symbol} at {interval} or under it"


def drawn(
    source: str,
    note: str,
    bars: list[Bar],
    interval: Interval,
    indicators: str,
    *,
    keep: int = 0,
) -> CandlesResponse:
    """Candles, with whatever lines were asked for drawn over them.

    `keep` trims to the last N bars *after* the indicators are computed, not
    before. An EMA-50 over the last 180 bars is not the EMA-50 of that series -
    its first fifty values are warming up - and a chart that silently showed a
    different line than a rule would trade on would be worse than no line.
    """
    wanted = parse_wanted(indicators, interval) if indicators else []
    lines = compute(bars, interval, wanted)
    cut = len(bars) - keep if keep and keep < len(bars) else 0
    return CandlesResponse(
        source=source,
        note=note,
        candles=[
            CandleResponse(
                at=b.ts.isoformat(),
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
            )
            for b in bars[cut:]
        ],
        lines=[
            LineResponse(
                label=line.label,
                name=line.name,
                length=line.length,
                interval=str(line.interval) if line.interval else None,
                on_price=line.on_price,
                values=line.values[cut:],
            )
            for line in lines
        ],
    )

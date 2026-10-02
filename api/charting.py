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

from typing import Protocol

from pydantic import BaseModel

from backtest.lines import compute, parse_wanted
from backtest.resample import resample
from marketdata import Interval
from marketdata.models import Bar
from marketdata.service import BarsResult


class Bars(Protocol):
    """What a chart needs of a bar store: freshen a series, and read one.

    A protocol rather than `BarService` itself, because those two calls are the
    whole of the contract and saying so is what makes the split below testable -
    the interesting behaviour here is *which* series gets freshened and which gets
    read, and that should be checkable without a store on disk.
    """

    def bars(
        self, symbol: str, interval: Interval, days: int, *, source: str | None = ...
    ) -> BarsResult: ...

    def stored(
        self, source: str, symbol: str, interval: Interval, *, days: int = ...
    ) -> BarsResult: ...


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


#: Sizes a source is asked for directly. Every other size is combined from the
#: largest of these below it.
#:
#: Fyers serves every resolution the desk names, which is the trap rather than the
#: convenience: a fetched four-hour series and fifteen-minute bars combined into
#: four hours can disagree about where a boundary falls, and nothing on a chart
#: would ever show it. The backfill stores fifteen minutes and a day; this says the
#: live refresh does the same, so what is on screen and the history behind it are
#: the same arithmetic.
#:
#: A source that is not listed is asked for whatever size is wanted, which is what
#: a venue serving its own candles at every size wants - see `marketdata/venue.py`.
SERVED: dict[str, tuple[Interval, ...]] = {
    "fyers": (Interval.M15, Interval.D1),
}


#: Bars of the fetched size to ask for when refreshing a series.
#:
#: The tail, not the chart's whole window. The store holds the depth a backfill put
#: there and what a live chart is missing is the last few bars, so asking for the
#: window would be both wasteful and, past a point, refused: Fyers serves a hundred
#: days of intraday or a year of daily per request, and a four-hour chart's window
#: is wider than the first of those. Sixty bars is enough slack to cover a weekend,
#: a holiday and a desk that was switched off for a few days.
TAIL_BARS = 60


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


def fetched_size(source: str, interval: Interval) -> Interval:
    """Which size to ask this source for, to have `interval` be current.

    Usually the size itself. For a source that serves only some of them, the
    largest it does serve below the one being looked at - because that is the
    series the resampling reads, and a four-hour chart is only as fresh as the
    fifteen-minute bars it is built from. Refreshing the size on screen rather
    than the one underneath it was the whole of the bug: nothing ever asked for
    fifteen-minute bars unless you happened to be looking at the fifteen-minute
    chart.
    """
    served = SERVED.get(source)
    if served is None or interval in served:
        return interval
    below = [s for s in served if s.seconds < interval.seconds]
    return max(below, key=lambda s: s.seconds) if below else interval


def series_for(
    service: Bars,
    source: str,
    symbol: str,
    interval: Interval,
    days: int,
    *,
    refresh: bool = True,
) -> tuple[list[Bar], str]:
    """Bars at one size, and a note about where they came from.

    The refresh and the read are two steps rather than one. First the source is
    asked for the tail of whatever size it serves, which brings the store up to
    date; then the window being drawn is read off disk, at the size asked for or
    combined up from a smaller one. Splitting them is what lets a chart show years
    of history whose last hour came from the venue a minute ago - a single fetch
    can only be one window, and the two wanted here are a few days and a few years.

    Two ways to read, in order of preference:

    1. Straight off disk at that exact size, for a series a backfill writes.
    2. Off disk at the largest smaller size held, combined up.

    The second is how the options desk draws anything but a day and a week: NSE
    bars are backfilled daily and at fifteen minutes, and every other size is
    arithmetic on those.
    """
    note = ""
    # The series is keyed by the source's own name for the instrument, which the
    # refresh reports back. Taken from there rather than assumed to be `symbol`,
    # so a source whose names differ from the desk's is read at the key it wrote.
    key = symbol
    if refresh:
        base = fetched_size(source, interval)
        got = service.bars(symbol, base, days_for(base, TAIL_BARS), source=source)
        key = got.series.symbol
        # Only a source that could not be read. "Held off" and the rest are the
        # cache working, and a chart that says so every two minutes is a chart
        # whose notes nobody reads.
        if not got.ok:
            note = got.note

    held = service.stored(source, key, interval, days=days)
    if held.bars:
        return held.bars, note

    for base in sorted(BASES, key=lambda b: b.seconds, reverse=True):
        if base.seconds >= interval.seconds:
            continue
        under = service.stored(source, key, base, days=days)
        if under.bars:
            built = f"built from {base} bars"
            return resample(under.bars, interval), f"{note} {built}" if note else built

    return [], note or f"Nothing stored for {symbol} at {interval} or under it"


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

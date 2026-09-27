"""Building a higher timeframe out of the bars underneath it.

A rule that wants an hourly trend and five-minute entries could have both series
fetched. It should not. Two fetched series can disagree: one can have a gap the
other does not, their boundaries can be an offset apart, and a source can revise
one and not the other. None of that is visible in a result - it shows up as a
trend filter that was reading a slightly different hour than the chart did.

Resampling makes it arithmetic instead. The hourly bar is *defined* as the twelve
five-minute bars inside it, so the two cannot disagree, and a gap in the base
series is a gap in both rather than a silent difference between them.

Buckets are aligned to the epoch, so an hourly bucket starts on the hour and a
daily one at midnight UTC. That is right for a market that never closes, which is
what this is being built for. An instrument with a session - an Indian index, a
stock - needs its day to start when its exchange opens, and `bucket_for` is where
that belongs when the time comes.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from marketdata.models import Bar, Interval


def bucket_for(at: datetime, interval: Interval) -> datetime:
    """The start of the bar of `interval` that contains this moment."""
    seconds = int(at.timestamp()) // interval.seconds * interval.seconds
    return datetime.fromtimestamp(seconds, tz=UTC)


def resample(bars: Sequence[Bar], to: Interval) -> list[Bar]:
    """Base bars, combined into bars of a longer interval.

    The last bucket may be incomplete - the hour is not over. It is returned all
    the same, because whether a bar has closed depends on the moment it is being
    read at, which this function has no opinion about. `View` decides that, and it
    decides it by comparing times rather than by counting bars, so a bucket that
    is short because the exchange was down is still treated as closed once its
    hour has passed.
    """
    if not bars:
        return []

    out: list[Bar] = []
    start = bucket_for(bars[0].ts, to)
    opening = bars[0].open
    high = bars[0].high
    low = bars[0].low
    close = bars[0].close
    volume = 0.0

    for bar in bars:
        at = bucket_for(bar.ts, to)
        if at != start:
            out.append(
                Bar(ts=start, open=opening, high=high, low=low, close=close, volume=volume)
            )
            start, opening, high, low, volume = at, bar.open, bar.high, bar.low, 0.0
        else:
            high = max(high, bar.high)
            low = min(low, bar.low)
        close = bar.close
        volume += bar.volume

    out.append(Bar(ts=start, open=opening, high=high, low=low, close=close, volume=volume))
    return out


def closes_at(bar_start: datetime, interval: Interval) -> datetime:
    """When a bar of this interval that started here is finished.

    A bar is stamped with its start, so the moment its information exists is one
    interval later. Every judgement about what was knowable when comes back to
    this, which is why it is a named function rather than an addition written out
    in three places.
    """
    return bar_start + timedelta(seconds=interval.seconds)

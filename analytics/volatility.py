"""How much a thing moves, and what the market charges to insure it.

Four different numbers get called "volatility" and conflating them is how a
premium seller talks themselves into a trade:

    realised     what the price actually did, annualised
    implied      what options are priced at, from the chain
    rank         where implied sits in its own past range
    the spread   implied minus realised, which is the edge being sold

All of them are annualised percentages, so they compare. A realised figure of
9% and an implied of 12% means options are charging three points more than the
recent past would justify - which is the usual state of affairs and the reason
selling premium is a business at all.

Measured on India VIX against the twenty-one sessions that followed it, over
469 overlapping windows: implied exceeded realised 79% of the time, by a median
of three vol points. That is the edge, and it is also why a rank matters more
than a level - the premium is nearly always there, so the question is whether
today's is large or small by its own standards.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from marketdata.models import Bar

#: Trading days in a year, for annualising. 252 is the convention; NSE runs
#: about 250, and the difference moves a vol figure by a fifth of a point.
YEAR = 252


def close_to_close(closes: Sequence[float], window: int = 20) -> float | None:
    """Annualised standard deviation of daily log returns.

    The textbook estimator and the noisiest one: it sees only the close, so a
    day that travelled two percent and came back reads as a quiet day. Kept
    because it is what everyone means by "realised volatility" and what an
    implied figure is quoted against.

    The sample standard deviation, dividing by n-1. Worth stating because the
    choice is visible: this desk once had two implementations of this function,
    one using n-1 and one using n, and the same index showed 9.3 in the context
    strip and 9.03 in the volatility panel. Over twenty samples the two differ
    by root(20/19), which is 2.6% - small enough to look like a rounding
    difference and large enough to make two figures on one screen disagree.
    """
    if window < 2 or len(closes) < window + 1:
        return None
    returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(len(closes) - window, len(closes))
        if closes[i] > 0 and closes[i - 1] > 0
    ]
    if len(returns) < 3:
        return None
    return statistics.stdev(returns) * math.sqrt(YEAR) * 100.0


def iv_hv_ratio(implied: float | None, realised: float | None) -> float | None:
    """Implied over realised. Above one means options cost more than the recent
    past would justify.

    A ratio rather than a difference, because it travels. Two points of premium
    on a 9% index is a quarter again on top; the same two points on a 25% index
    is almost nothing, and a subtraction calls them equal. The difference is
    still worth showing beside it - it is what a seller actually collects - but
    the ratio is what compares across instruments and across regimes.

    None when realised is zero or missing: a ratio against a market that has not
    moved is infinite rather than excellent.
    """
    if implied is None or realised is None or realised <= 0:
        return None
    return implied / realised


def parkinson(bars: Sequence[Bar], window: int = 20) -> float | None:
    """Annualised volatility from the daily range rather than the close.

    Roughly five times more efficient than close-to-close, because a day's high
    and low carry more information about how far price travelled than its two
    endpoints do. It runs lower on a market that gaps, since a gap happens
    between sessions and no intraday range contains it - so a Parkinson figure
    well under the close-to-close one is itself a reading: the movement is
    arriving overnight.
    """
    if window < 1 or len(bars) < window:
        return None
    window_bars = [b for b in bars[-window:] if b.high > 0 and b.low > 0]
    if not window_bars:
        return None
    total = sum(math.log(b.high / b.low) ** 2 for b in window_bars)
    variance = total / (4.0 * math.log(2.0) * len(window_bars))
    return math.sqrt(variance * YEAR) * 100.0


@dataclass(frozen=True)
class Rank:
    """Where a reading sits among its own past.

    Two figures because they disagree usefully. `rank` is the position within the
    high-low range, so one spike two years ago flattens everything since.
    `percentile` is the share of days that were lower, which ignores how far away
    the extremes are and answers "is this unusual" rather than "how extreme".
    """

    value: float
    low: float
    high: float
    rank: float
    percentile: float
    days: int

    @property
    def says(self) -> str:
        if self.percentile >= 70:
            return "rich by its own history"
        if self.percentile <= 30:
            return "cheap by its own history"
        return "middling by its own history"


def rank_of(value: float, history: Sequence[float]) -> Rank | None:
    """Where `value` stands in `history`. None when there is not enough of it.

    Thirty readings is the floor, and it is a low one: a rank over two months of
    history is a statement about two months. The count comes back with the figure
    so a screen can say how much is behind it rather than presenting a number
    built on a fortnight as though it were built on a year.
    """
    usable = [v for v in history if math.isfinite(v)]
    if len(usable) < 30:
        return None
    low, high = min(usable), max(usable)
    span = high - low
    return Rank(
        value=value,
        low=low,
        high=high,
        # A flat history has no range to sit in; the middle is the only honest
        # answer rather than a division by zero.
        rank=50.0 if span <= 0 else (value - low) / span * 100.0,
        percentile=sum(1 for v in usable if v < value) / len(usable) * 100.0,
        days=len(usable),
    )


def expected_move(straddle: float, spot: float) -> float | None:
    """What the at-the-money straddle says the market expects, as a percentage.

    Not a standard deviation and not annualised: it is the move between now and
    this expiry that the options are priced to cover. A seller of that straddle
    is short exactly this number.
    """
    if spot <= 0 or straddle <= 0:
        return None
    return straddle / spot * 100.0

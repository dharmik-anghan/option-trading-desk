"""Higher highs and lower lows: what the price has been doing, in words.

A swing high is a bar whose high beats the `k` bars either side of it. String
those together and the last two highs and the last two lows describe the market:
higher highs with higher lows is an uptrend, lower highs with lower lows a
downtrend, and the two mixed cases are a range widening or tightening.

**The catch, which is the whole reason this file is careful.** A swing high
cannot be recognised until `k` bars have printed after it. There is no way round
that - it is the definition - so any live reading is `k` bars behind, and the
most recent turn is always a candidate rather than a fact. A panel that showed
the candidate as though it were confirmed would be reporting a structure that
the next bar can revoke. Both are returned, and which is which is a field.

That is the same discipline the backtest engine is built on, for the same
reason: a structure read from bars that had not finished is a structure nobody
could have traded.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from marketdata.models import Bar

#: Bars either side of a swing. Two is about one swing a week on a daily series,
#: which is the scale most people read structure at; larger means fewer and more
#: significant turns, and a longer wait before any of them is confirmed.
DEFAULT_K = 2


class Kind(StrEnum):
    HIGH = "high"
    LOW = "low"


class Trend(StrEnum):
    """What the last two highs and the last two lows say together."""

    UP = "uptrend"
    DOWN = "downtrend"
    #: Higher high and lower low: the range is widening, which is neither side
    #: winning and is usually the least tradeable state there is.
    BROADENING = "broadening"
    #: Lower high and higher low: coiling into a smaller range.
    CONTRACTING = "contracting"
    #: Not enough swings yet to say anything.
    UNCLEAR = "unclear"


@dataclass(frozen=True)
class Swing:
    """One turn."""

    at: datetime
    kind: Kind
    price: float
    #: Index into the bars it was found in, so a chart can place it.
    index: int
    #: False while fewer than `k` bars have printed after it. A provisional
    #: swing is the market's current candidate for a turn and the next bar can
    #: take it away.
    confirmed: bool


@dataclass(frozen=True)
class Break:
    """Price closing through the level a previous swing set."""

    at: datetime
    price: float
    #: The swing level that was taken out.
    level: float
    #: When that level was set. A break is a span between two moments - the
    #: swing that made the level and the bar that took it - and drawing it as a
    #: line across the whole chart states it at times when it had not happened
    #: and times when it was long over.
    from_at: datetime
    #: True when the break continues the prevailing structure - a higher high in
    #: an uptrend. False when it goes against it, which is the first sign of a
    #: turn and is usually called a change of character.
    continuation: bool


@dataclass(frozen=True)
class Structure:
    """What the swings add up to."""

    swings: tuple[Swing, ...]
    trend: Trend
    #: "HH" or "LH", and "HL" or "LL". None until there are two of each.
    high_label: str | None
    low_label: str | None
    #: Every close through a swing level in these bars, oldest first. One was
    #: reported before, and only if the *latest* high or low happened to have
    #: been taken out - so a market in a clean uptrend, which breaks a level
    #: every few bars, drew nothing at all between the last one and now.
    breaks: tuple[Break, ...]

    @property
    def last_break(self) -> Break | None:
        """The most recent one, which is what a sentence about this market means."""
        return self.breaks[-1] if self.breaks else None

    @property
    def confirmed(self) -> tuple[Swing, ...]:
        return tuple(s for s in self.swings if s.confirmed)

    @property
    def provisional(self) -> Swing | None:
        """The candidate turn, if there is one. Always the last."""
        return next((s for s in reversed(self.swings) if not s.confirmed), None)

    @property
    def says(self) -> str:
        if self.high_label is None or self.low_label is None:
            return "not enough swings to say"
        return f"{self.high_label} + {self.low_label}"


def swings(bars: Sequence[Bar], k: int = DEFAULT_K) -> list[Swing]:
    """Every turn in these bars, oldest first.

    A bar can be both the highest and the lowest of its window only if the
    window is flat, which is not a turn - so a tie is not a swing. Strictly
    beating the neighbours on both sides is what makes it one.

    The tail is scanned separately for a candidate: a bar that leads the `k`
    before it and everything since, but has not yet had `k` bars printed after
    it to confirm it.
    """
    if k < 1:
        raise ValueError("a swing needs at least one bar either side")
    found: list[Swing] = []
    for i in range(k, len(bars) - k):
        before = bars[i - k : i]
        after = bars[i + 1 : i + 1 + k]
        here = bars[i]
        if here.high > max(b.high for b in before) and here.high > max(b.high for b in after):
            found.append(
                Swing(at=here.ts, kind=Kind.HIGH, price=here.high, index=i, confirmed=True)
            )
        elif here.low < min(b.low for b in before) and here.low < min(b.low for b in after):
            found.append(
                Swing(at=here.ts, kind=Kind.LOW, price=here.low, index=i, confirmed=True)
            )

    candidate = _provisional(bars, k, found)
    if candidate is not None:
        found.append(candidate)
    return found


def _provisional(bars: Sequence[Bar], k: int, confirmed: list[Swing]) -> Swing | None:
    """The turn the market is currently making, if it is making one.

    Looks only at the tail that could not be confirmed: a bar with `k` bars
    before it that it beats, and fewer than `k` after. It is reported so a
    reader can see what is forming, and flagged so nobody mistakes it for
    settled.
    """
    start = max(k, len(bars) - k)
    last = confirmed[-1].index if confirmed else -1
    for i in range(len(bars) - 1, start - 1, -1):
        if i <= last or i < k:
            continue
        before = bars[i - k : i]
        after = bars[i + 1 :]
        here = bars[i]
        beats_high = here.high > max(b.high for b in before) and all(
            here.high > b.high for b in after
        )
        beats_low = here.low < min(b.low for b in before) and all(here.low < b.low for b in after)
        if beats_high:
            return Swing(at=here.ts, kind=Kind.HIGH, price=here.high, index=i, confirmed=False)
        if beats_low:
            return Swing(at=here.ts, kind=Kind.LOW, price=here.low, index=i, confirmed=False)
    return None


def read(bars: Sequence[Bar], k: int = DEFAULT_K) -> Structure:
    """The structure these bars are in.

    Labelled from confirmed swings only. A provisional turn is carried alongside
    so a chart can draw it, but it does not get a vote on what the trend is -
    letting it would mean the label changed on a bar that has not finished
    behaving.
    """
    found = swings(bars, k)
    settled = [s for s in found if s.confirmed]
    highs = [s for s in settled if s.kind is Kind.HIGH]
    lows = [s for s in settled if s.kind is Kind.LOW]

    high_label = _label(highs, "HH", "LH")
    low_label = _label(lows, "HL", "LL")
    trend = _trend(high_label, low_label)

    return Structure(
        swings=tuple(found),
        trend=trend,
        high_label=high_label,
        low_label=low_label,
        breaks=tuple(breaks(bars, settled, k)),
    )


def _label(points: list[Swing], higher: str, lower: str) -> str | None:
    if len(points) < 2:
        return None
    return higher if points[-1].price > points[-2].price else lower


def _trend(high_label: str | None, low_label: str | None) -> Trend:
    return {
        ("HH", "HL"): Trend.UP,
        ("LH", "LL"): Trend.DOWN,
        ("HH", "LL"): Trend.BROADENING,
        ("LH", "HL"): Trend.CONTRACTING,
    }.get((high_label or "", low_label or ""), Trend.UNCLEAR)


def breaks(bars: Sequence[Bar], settled: Sequence[Swing], k: int) -> list[Break]:
    """Every close through a swing level, oldest first.

    On a close rather than a touch. A wick through a level is a test of it; a
    close beyond is the market agreeing, and the difference is most of what
    separates a break from a stop hunt.

    Causal, with the same discipline as everything else here: a swing found at
    index `i` is not used as a level until bar `i + k`, because that is the
    first bar by which anybody could have known it was a swing. Reading the
    whole series first and then asking which bars closed through which levels
    would draw breaks of levels that had not been established yet.

    A level is broken once. Without that, a market that runs away from a swing
    high reports a break on every bar after it, and the chart becomes a row of
    identical lines rather than a record of the moments something gave way.

    Whether a break continues the structure or cracks it is judged on the trend
    *at that bar*, not on the trend now - a change of character is interesting
    because of what it did to the reading at the time.
    """
    ordered = sorted(settled, key=lambda s: s.index)
    found: list[Break] = []
    highs: list[Swing] = []
    lows: list[Swing] = []
    high: Swing | None = None
    low: Swing | None = None
    taken_high = taken_low = False
    nxt = 0

    for i, bar in enumerate(bars):
        # Swings become usable k bars after they print, which is when they could
        # first have been recognised.
        while nxt < len(ordered) and ordered[nxt].index + k <= i:
            swing = ordered[nxt]
            if swing.kind is Kind.HIGH:
                highs.append(swing)
                high, taken_high = swing, False
            else:
                lows.append(swing)
                low, taken_low = swing, False
            nxt += 1

        trend = _trend(_label(highs, "HH", "LH"), _label(lows, "HL", "LL"))
        if high is not None and not taken_high and bar.close > high.price:
            found.append(
                Break(
                    at=bar.ts,
                    price=bar.close,
                    level=high.price,
                    from_at=high.at,
                    continuation=trend is not Trend.DOWN,
                )
            )
            taken_high = True
        if low is not None and not taken_low and bar.close < low.price:
            found.append(
                Break(
                    at=bar.ts,
                    price=bar.close,
                    level=low.price,
                    from_at=low.at,
                    continuation=trend is not Trend.UP,
                )
            )
            taken_low = True
    return found

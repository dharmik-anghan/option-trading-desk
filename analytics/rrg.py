"""Relative rotation: where something stands against a benchmark, and which way
it is heading.

Two numbers per security, both centred on 100.

    RS-Ratio      is it stronger or weaker than the benchmark
    RS-Momentum   is that strength growing or fading

Plotted against each other they make four quadrants, and things tend to travel
round them clockwise: a laggard starts improving, becomes a leader, then weakens,
then lags again. The point of the picture is the rotation rather than the
position - a sector at 101 and climbing is a different proposition from one at
105 and falling, and a table of relative returns says the same thing about both.

    momentum
       ^
       |  improving  |  leading
    100|-------------+-------------
       |  lagging    |  weakening
       +-------------+-------------> ratio
                    100

**On the formula.** The original is Julius de Kempenaer's and its exact
constants are not published, so this is the reconstruction everyone else uses:
relative strength against its own trailing average, turned into a z-score and
recentred on 100. It gives the same rotation and the same quadrants; it will not
give the same decimals as a vendor's chart, and anything built on it should not
be reconciled against one to the second decimal place.

Nothing here knows about India, or sectors, or what a benchmark is - it takes two
price series and returns two lines.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from analytics.indicators import Line

#: Periods the ratio and the momentum are normalised over. Fourteen is the
#: common default for daily; the same number on weekly bars is a quarter of a
#: year, which is the horizon the picture is usually read at.
DEFAULT_WINDOW = 14


class Quadrant(StrEnum):
    """Where a point sits. Named as they are on every RRG ever drawn."""

    LEADING = "leading"
    WEAKENING = "weakening"
    LAGGING = "lagging"
    IMPROVING = "improving"

    @staticmethod
    def of(ratio: float, momentum: float) -> Quadrant:
        if ratio >= 100.0:
            return Quadrant.LEADING if momentum >= 100.0 else Quadrant.WEAKENING
        return Quadrant.IMPROVING if momentum >= 100.0 else Quadrant.LAGGING


@dataclass(frozen=True)
class Point:
    """One security at one moment."""

    at: datetime
    ratio: float
    momentum: float

    @property
    def quadrant(self) -> Quadrant:
        return Quadrant.of(self.ratio, self.momentum)

    @property
    def distance(self) -> float:
        """How far from the centre. Long tails far out are the interesting ones."""
        return math.hypot(self.ratio - 100.0, self.momentum - 100.0)


def relative_strength(prices: Sequence[float], benchmark: Sequence[float]) -> list[float]:
    """Price over benchmark, scaled to 100 at the start.

    Scaled rather than raw so two securities at very different prices produce
    comparable lines - the level of the ratio is meaningless, only its shape is.
    Zero or negative benchmark values are skipped by carrying the previous value,
    because a gap in the benchmark should not put a hole in every security.
    """
    out: list[float] = []
    carried = 100.0
    base: float | None = None
    for price, mark in zip(prices, benchmark, strict=True):
        if mark > 0 and price > 0:
            raw = price / mark
            if base is None:
                base = raw
            carried = 100.0 * raw / base if base else carried
        out.append(carried)
    return out


def _zscore(values: Sequence[float], window: int) -> Line:
    """Each value against the mean and spread of the `window` before it.

    Including itself in the window, which is what makes the newest point able to
    sit at an extreme rather than always being pulled toward the middle of its
    own sample.
    """
    out: Line = [None] * len(values)
    for i in range(window - 1, len(values)):
        sample = values[i - window + 1 : i + 1]
        mean = sum(sample) / window
        variance = sum((v - mean) ** 2 for v in sample) / window
        spread = math.sqrt(variance)
        # A window that is flat to within floating-point dust has no deviation
        # to measure, and dividing by its spread amplifies the dust into a
        # reading. A security trending at a perfectly constant rate produces
        # exactly this: a relative-strength ratio identical to fourteen decimal
        # places, whose z-score came out as a momentum swinging between 98.8 and
        # 101.4 - pure noise, and noise that lands either side of a quadrant
        # boundary. Compared against the magnitude of the values rather than
        # against zero, because the strength series is around 100 rather than
        # around 1.
        negligible = max(abs(mean), 1.0) * 1e-9
        out[i] = 0.0 if spread <= negligible else (values[i] - mean) / spread
    return out


def rrg(
    prices: Sequence[float],
    benchmark: Sequence[float],
    times: Sequence[datetime],
    window: int = DEFAULT_WINDOW,
) -> list[Point]:
    """The rotation path for one security against one benchmark.

    Both numbers are the same measurement applied twice: how far something stands
    above or below its own recent normal, in standard deviations, recentred on
    100. The ratio applies it to relative strength, and the momentum applies it to
    the ratio - so momentum is "is the relative strength unusual *for itself*",
    which is what makes it turn before the ratio does.

    Deliberately not relative strength divided by its own average. That reads as
    the obvious thing to do and it is wrong: for a security declining at a steady
    rate, the quotient is a *constant* below one, so normalising it produces
    exactly zero and puts a consistent underperformer at dead centre. The level
    has to survive the normalisation, and a z-score of the strength itself is what
    keeps it.

    Returns only the points that exist: the ratio needs `window` periods and the
    momentum needs another `window` on top, so a series shorter than roughly twice
    the window produces nothing rather than a short line of guesses.
    """
    if window < 2:
        raise ValueError("a window of fewer than two periods normalises nothing")
    if not (len(prices) == len(benchmark) == len(times)):
        raise ValueError("prices, benchmark and times must be the same length")

    strength = relative_strength(prices, benchmark)

    ratio: Line = [
        None if z is None else 100.0 + z for z in _zscore(strength, window)
    ]

    # The same treatment, applied to the ratio. Not its slope: a slope on a daily
    # series is mostly noise, where "unusually high for itself" is a statement
    # about the last few weeks.
    defined = [i for i, v in enumerate(ratio) if v is not None]
    momentum: Line = [None] * len(prices)
    if len(defined) >= window:
        first = defined[0]
        scored = _zscore([v for v in ratio[first:] if v is not None], window)
        for offset, z in enumerate(scored):
            momentum[first + offset] = None if z is None else 100.0 + z

    return [
        Point(at=times[i], ratio=r, momentum=m)
        for i, (r, m) in enumerate(zip(ratio, momentum, strict=True))
        if r is not None and m is not None
    ]

"""Relative rotation: the quadrants, the rotation, and what it refuses to guess."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

import pytest

from analytics.rrg import Point, Quadrant, relative_strength, rrg

START = datetime(2026, 1, 1, tzinfo=UTC)


def _days(n: int) -> list[datetime]:
    return [START + timedelta(days=i) for i in range(n)]


def _flat(n: int, at: float = 100.0) -> list[float]:
    return [at] * n


def test_the_four_quadrants_are_named_as_they_are_drawn() -> None:
    assert Quadrant.of(101.0, 101.0) is Quadrant.LEADING
    assert Quadrant.of(101.0, 99.0) is Quadrant.WEAKENING
    assert Quadrant.of(99.0, 99.0) is Quadrant.LAGGING
    assert Quadrant.of(99.0, 101.0) is Quadrant.IMPROVING


def test_the_centre_counts_as_leading() -> None:
    """A boundary has to fall somewhere, and a point exactly at 100/100 is not
    lagging by any reading."""
    assert Quadrant.of(100.0, 100.0) is Quadrant.LEADING


def test_relative_strength_starts_at_one_hundred() -> None:
    """The level means nothing, only the shape - so two securities at very
    different prices produce comparable lines."""
    cheap = relative_strength([10.0, 11.0, 12.0], [100.0, 100.0, 100.0])
    dear = relative_strength([1000.0, 1100.0, 1200.0], [100.0, 100.0, 100.0])

    assert cheap[0] == pytest.approx(100.0)
    assert cheap == pytest.approx(dear)


def test_relative_strength_rises_when_the_security_outruns_the_benchmark() -> None:
    line = relative_strength([100.0, 110.0], [100.0, 100.0])

    assert line[1] > line[0]


def test_relative_strength_falls_when_the_benchmark_outruns_it() -> None:
    line = relative_strength([100.0, 100.0], [100.0, 110.0])

    assert line[1] < line[0]


def test_a_missing_benchmark_carries_the_last_value_rather_than_dividing_by_zero() -> None:
    """A hole in the benchmark should not put a hole in every security."""
    line = relative_strength([100.0, 100.0, 100.0], [100.0, 0.0, 100.0])

    assert line[1] == line[0]
    assert all(math.isfinite(v) for v in line)


def test_a_series_too_short_to_normalise_produces_nothing() -> None:
    """Rather than a short line of guesses. Both lines need a window, and the
    momentum needs another on top of the ratio."""
    n = 10
    points = rrg(_flat(n, 110.0), _flat(n), _days(n), window=14)

    assert points == []


def _drifting(n: int, drift: float, seed: int = 3) -> list[float]:
    """A price that trends with noise on it, which is what a price is.

    Seeded, so a failure is a failure rather than a coin toss.
    """
    rng = random.Random(seed)
    price = 100.0
    out: list[float] = []
    for _ in range(n):
        price *= 1 + drift + rng.gauss(0, 0.004)
        out.append(price)
    return out


def test_an_outperformer_sits_on_the_right_of_the_picture() -> None:
    """The simplest thing it has to get right: stronger than the benchmark is a
    ratio above 100, whatever the momentum is doing."""
    n = 80
    points = rrg(_drifting(n, 0.004), _flat(n), _days(n), window=10)

    assert points
    assert points[-1].ratio > 100.0
    assert points[-1].quadrant in (Quadrant.LEADING, Quadrant.WEAKENING)


def test_an_underperformer_sits_on_the_left_of_the_picture() -> None:
    n = 80
    points = rrg(_drifting(n, -0.004), _flat(n), _days(n), window=10)

    assert points[-1].ratio < 100.0
    assert points[-1].quadrant in (Quadrant.LAGGING, Quadrant.IMPROVING)


def test_a_perfectly_constant_rate_of_decline_sits_on_the_momentum_axis() -> None:
    """The degenerate case, pinned because it looks like a bug and is not.

    A security declining at exactly the same rate every day has a relative
    strength whose deviation from its own normal never changes - so its momentum
    genuinely is not moving, and the point sits exactly on the 100 line. Real
    prices have noise and never do this; a test series made from a geometric
    progression does it every time.
    """
    n = 80
    points = rrg([100.0 * (0.996**i) for i in range(n)], _flat(n), _days(n), window=10)

    assert points[-1].ratio < 100.0
    assert points[-1].momentum == pytest.approx(100.0)


def test_a_security_that_moves_with_its_benchmark_stays_near_the_centre() -> None:
    """Identical series have no relative strength to measure at all."""
    n = 80
    both = [100.0 * (1.002**i) for i in range(n)]

    points = rrg(both, both, _days(n), window=10)

    assert all(p.distance < 1e-6 for p in points)


def test_a_turn_from_lagging_to_improving_shows_as_momentum_crossing_first() -> None:
    """The rotation itself: momentum leads the ratio, which is the whole reason
    for plotting two numbers rather than one.

    A security that falls behind for months and then turns up is still below the
    benchmark on the ratio while the momentum has already crossed - which is what
    "improving" means and what a table of relative returns cannot show.
    """
    n = 140
    turn = 80
    falling = _drifting(turn, -0.005, seed=11)
    rising = _drifting(n - turn, 0.02, seed=12)
    prices = falling + [falling[-1] * (p / 100.0) for p in rising]

    points = rrg(prices, _flat(n), _days(n), window=10)

    # The rotation, in order. Somewhere after the turn it is still behind the
    # benchmark while its momentum has already crossed - which is what
    # "improving" means, and what a table of relative returns cannot show. Then
    # the ratio catches up and it leads.
    def first(quadrant: Quadrant) -> int:
        return next(i for i, p in enumerate(points) if p.quadrant is quadrant)

    assert first(Quadrant.LAGGING) < first(Quadrant.IMPROVING) < first(Quadrant.LEADING)


def test_points_come_back_in_time_order_with_their_dates() -> None:
    n = 80
    points = rrg([100.0 + i for i in range(n)], _flat(n), _days(n), window=10)

    assert [p.at for p in points] == sorted(p.at for p in points)
    assert points[-1].at == START + timedelta(days=n - 1)


def test_distance_from_the_centre() -> None:
    assert Point(START, 103.0, 96.0).distance == pytest.approx(5.0)


def test_mismatched_series_are_refused() -> None:
    with pytest.raises(ValueError, match="same length"):
        rrg([1.0, 2.0], [1.0], [START, START], window=5)


@pytest.mark.parametrize("window", [0, 1])
def test_a_window_that_normalises_nothing_is_refused(window: int) -> None:
    with pytest.raises(ValueError, match="normalises nothing"):
        rrg(_flat(50), _flat(50), _days(50), window=window)

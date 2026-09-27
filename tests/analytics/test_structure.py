"""Higher highs and lower lows, and the turn that is not confirmed yet."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from analytics.structure import Kind, Trend, read, swings
from marketdata.models import Bar

START = datetime(2026, 1, 1, tzinfo=UTC)


def _bars(highs_lows: list[tuple[float, float]]) -> list[Bar]:
    """Bars from (high, low), closing in the middle."""
    return [
        Bar(
            ts=START + timedelta(days=i),
            open=(h + lo) / 2,
            high=h,
            low=lo,
            close=(h + lo) / 2,
            volume=1.0,
        )
        for i, (h, lo) in enumerate(highs_lows)
    ]


def _peak(at: int, of: int, height: float = 10.0) -> list[tuple[float, float]]:
    """A series with a single peak at `at`, for pinning the detector."""
    return [(100.0 + (height if i == at else 0.0), 90.0) for i in range(of)]


def test_a_peak_is_a_swing_high() -> None:
    found = swings(_bars(_peak(at=5, of=11)), k=2)

    highs = [s for s in found if s.kind is Kind.HIGH]
    assert len(highs) == 1
    assert highs[0].price == 110
    assert highs[0].index == 5


def test_a_trough_is_a_swing_low() -> None:
    rows = [(100.0, 90.0 - (10.0 if i == 5 else 0.0)) for i in range(11)]

    found = swings(_bars(rows), k=2)

    lows = [s for s in found if s.kind is Kind.LOW]
    assert len(lows) == 1
    assert lows[0].price == 80


def test_a_tie_is_not_a_turn() -> None:
    """A flat window is not a swing however you look at it: a bar can only be
    both the highest and the lowest of its neighbours if nothing moved."""
    assert swings(_bars([(100.0, 90.0)] * 11), k=2) == []


def test_a_larger_k_finds_fewer_and_more_significant_turns() -> None:
    """On a noisy series, not a regular one: peaks spaced exactly 2k+1 apart are
    each still the highest of their own window whatever k is, so a regular saw
    tooth shows no filtering at all and proves nothing."""
    import random

    rng = random.Random(4)
    price = 100.0
    rows: list[tuple[float, float]] = []
    for _ in range(200):
        price *= 1 + rng.gauss(0, 0.01)
        rows.append((price * 1.004, price * 0.996))
    bars = _bars(rows)

    assert len(swings(bars, k=1)) > len(swings(bars, k=4)) * 2


def test_k_must_leave_a_bar_on_each_side() -> None:
    with pytest.raises(ValueError, match="at least one bar"):
        swings(_bars(_peak(at=3, of=9)), k=0)


# --------------------------------------------------------------------------
# The confirmation lag, which is the whole point
# --------------------------------------------------------------------------


def test_a_turn_is_not_confirmed_until_k_bars_have_printed_after_it() -> None:
    """There is no way round this - it is the definition of a swing. A panel
    that showed the candidate as settled would report a structure the next bar
    can revoke."""
    # the peak is the second-to-last bar, so only one bar follows it
    rows = _peak(at=9, of=11)

    found = swings(_bars(rows), k=2)

    assert [s.confirmed for s in found] == [False]
    assert found[0].index == 9


def test_the_same_turn_becomes_confirmed_once_the_bars_arrive() -> None:
    forming = _bars(_peak(at=9, of=11))
    settled = _bars(_peak(at=9, of=13))

    assert swings(forming, k=2)[-1].confirmed is False
    assert swings(settled, k=2)[-1].confirmed is True


def test_a_provisional_turn_does_not_get_a_vote_on_the_trend() -> None:
    """Letting it would mean the label changing on a bar that has not finished
    behaving."""
    # four confirmed swings making an uptrend, then a fresh high at the end
    rows = [
        (100.0, 90.0), (102.0, 92.0), (98.0, 88.0), (104.0, 94.0),
        (100.0, 96.0), (99.0, 91.0), (106.0, 95.0), (103.0, 97.0),
    ]
    bars = _bars(rows)

    structure = read(bars, k=1)

    assert structure.provisional is None or structure.provisional.confirmed is False
    # the label comes from confirmed swings only
    for swing in structure.confirmed:
        assert swing.confirmed


# --------------------------------------------------------------------------
# What the swings add up to
# --------------------------------------------------------------------------


def _alternating(points: list[float]) -> list[Bar]:
    """Bars whose highs and lows alternate through the given turning points."""
    rows: list[tuple[float, float]] = []
    for i, level in enumerate(points):
        if i % 2 == 0:
            rows.extend([(level - 6, level - 10), (level, level - 10), (level - 6, level - 10)])
        else:
            rows.extend([(level + 10, level + 6), (level + 10, level), (level + 10, level + 6)])
    return _bars(rows)


def test_higher_highs_and_higher_lows_is_an_uptrend() -> None:
    structure = read(_alternating([100, 90, 110, 95, 120, 100]), k=1)

    assert structure.high_label == "HH"
    assert structure.low_label == "HL"
    assert structure.trend is Trend.UP
    assert structure.says == "HH + HL"


def test_lower_highs_and_lower_lows_is_a_downtrend() -> None:
    structure = read(_alternating([120, 100, 110, 95, 100, 90]), k=1)

    assert structure.trend is Trend.DOWN


def test_a_higher_high_with_a_lower_low_is_broadening() -> None:
    """Neither side winning, and usually the least tradeable state there is."""
    structure = read(_alternating([100, 95, 110, 90]), k=1)

    assert structure.trend is Trend.BROADENING


def test_a_lower_high_with_a_higher_low_is_contracting() -> None:
    structure = read(_alternating([110, 90, 100, 95]), k=1)

    assert structure.trend is Trend.CONTRACTING


def test_too_few_swings_says_so_rather_than_guessing() -> None:
    structure = read(_bars(_peak(at=5, of=11)), k=2)

    assert structure.trend is Trend.UNCLEAR
    assert structure.says == "not enough swings to say"


def test_structure_survives_a_series_with_no_bars() -> None:
    structure = read([], k=2)

    assert structure.swings == ()
    assert structure.trend is Trend.UNCLEAR

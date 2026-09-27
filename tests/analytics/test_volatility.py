"""The four things called volatility, and what they refuse to guess."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from analytics.volatility import (
    YEAR,
    close_to_close,
    expected_move,
    iv_hv_ratio,
    parkinson,
    rank_of,
)
from marketdata.models import Bar

START = datetime(2026, 1, 1, tzinfo=UTC)


def _bars(rows: list[tuple[float, float, float]]) -> list[Bar]:
    """Bars from (high, low, close)."""
    return [
        Bar(ts=START + timedelta(days=i), open=c, high=h, low=lo, close=c, volume=1.0)
        for i, (h, lo, c) in enumerate(rows)
    ]


def test_a_price_that_never_moves_has_no_volatility() -> None:
    assert close_to_close([100.0] * 40, 20) == pytest.approx(0.0)


def test_a_known_daily_move_annualises_to_the_expected_figure() -> None:
    """One percent a day, alternating, is about 16% a year.

    Worth pinning for two reasons. The annualisation factor is the easy thing to
    get wrong, and so is the estimator: this uses the *sample* standard
    deviation, dividing by n-1, so the answer is root(n/(n-1)) larger than the
    population one. The desk once had both, and the same index read 9.3 in one
    panel and 9.03 in the next.
    """
    closes = [100.0]
    for i in range(40):
        closes.append(closes[-1] * (1.01 if i % 2 else 1 / 1.01))

    vol = close_to_close(closes, 20)

    population = math.log(1.01) * math.sqrt(YEAR) * 100
    assert vol is not None
    assert vol == pytest.approx(population * math.sqrt(20 / 19), rel=1e-6)


def test_volatility_needs_a_full_window_rather_than_guessing() -> None:
    assert close_to_close([100.0, 101.0, 102.0], 20) is None


def test_parkinson_reads_the_range_where_close_to_close_reads_nothing() -> None:
    """A day that travelled two percent and came back is a quiet day to one
    estimator and a busy one to the other. That difference is the point."""
    rows = [(101.0, 99.0, 100.0)] * 25

    assert close_to_close([r[2] for r in rows], 20) == pytest.approx(0.0)
    assert (parkinson(_bars(rows), 20) or 0) > 10


def test_parkinson_runs_low_when_the_movement_arrives_overnight() -> None:
    """A gap happens between sessions, so no intraday range contains it - a
    Parkinson figure well under the close-to-close one is itself a reading."""
    rows: list[tuple[float, float, float]] = []
    close = 100.0
    for i in range(30):
        close *= 1.02 if i % 2 else 1 / 1.02
        # a tight range around a price that gapped to get there
        rows.append((close * 1.001, close * 0.999, close))
    bars = _bars(rows)

    assert (parkinson(bars, 20) or 0) < (close_to_close([b.close for b in bars], 20) or 0)


def test_a_rank_needs_enough_history_to_be_a_rank() -> None:
    """A figure built on a fortnight should not be presented as one built on a
    year."""
    assert rank_of(12.0, [10.0, 11.0, 12.0]) is None


def test_rank_and_percentile_disagree_usefully() -> None:
    """One spike flattens the range and leaves the percentile untouched.

    `rank` is position within the high-low span, so a single crisis two years ago
    pushes everything since into the bottom tenth. `percentile` counts days and
    ignores how far away the extreme is.
    """
    history = [10.0] * 50 + [90.0]

    ranked = rank_of(11.0, history)

    assert ranked is not None
    assert ranked.rank < 5
    assert ranked.percentile > 90


def test_a_flat_history_ranks_in_the_middle_rather_than_dividing_by_zero() -> None:
    ranked = rank_of(10.0, [10.0] * 40)

    assert ranked is not None
    assert ranked.rank == 50.0


def test_a_rank_says_how_much_is_behind_it() -> None:
    ranked = rank_of(12.0, [float(i % 20) for i in range(200)])

    assert ranked is not None
    assert ranked.days == 200


@pytest.mark.parametrize(
    ("percentile", "expected"),
    [(85.0, "rich"), (15.0, "cheap"), (50.0, "middling")],
)
def test_a_rank_reads_as_words(percentile: float, expected: str) -> None:
    history = [float(i) for i in range(100)]
    ranked = rank_of(percentile, history)

    assert ranked is not None
    assert expected in ranked.says


def test_the_expected_move_is_the_straddle_over_spot() -> None:
    """Not annualised and not a standard deviation: it is what the options are
    priced to cover between now and expiry, which is what a seller is short."""
    assert expected_move(196.0, 23140.5) == pytest.approx(0.847, abs=0.001)


def test_an_expected_move_on_nothing() -> None:
    assert expected_move(0.0, 100.0) is None
    assert expected_move(100.0, 0.0) is None


# --------------------------------------------------------------------------
# Implied against realised
# --------------------------------------------------------------------------


def test_the_ratio_says_how_much_dearer_options_are() -> None:
    """Options at 12 against a market moving at 9 cost a third again."""
    assert iv_hv_ratio(12.0, 9.0) == pytest.approx(4 / 3)


def test_the_ratio_travels_where_the_difference_does_not() -> None:
    """Two points of premium on a 9% index is a quarter again on top; the same
    two points on a 25% index is almost nothing. A subtraction calls them equal
    and a ratio does not - which is the whole reason for having both."""
    quiet = iv_hv_ratio(11.0, 9.0)
    wild = iv_hv_ratio(27.0, 25.0)

    assert quiet is not None and wild is not None
    assert (11.0 - 9.0) == (27.0 - 25.0)
    # 1.22 against 1.08: the same two points is a quarter again on a quiet
    # index and eight percent on a wild one.
    assert quiet == pytest.approx(11 / 9)
    assert wild == pytest.approx(27 / 25)
    assert quiet > wild


def test_a_ratio_against_a_market_that_has_not_moved_is_undefined() -> None:
    """Infinite rather than excellent."""
    assert iv_hv_ratio(12.0, 0.0) is None
    assert iv_hv_ratio(12.0, None) is None
    assert iv_hv_ratio(None, 9.0) is None

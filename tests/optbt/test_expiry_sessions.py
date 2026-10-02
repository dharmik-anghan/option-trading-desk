"""Distance to expiry in sessions, and the filter that keeps clear of its last two."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import duckdb
import pytest

from optbt.data.history import History
from optbt.market import View
from optbt.strategies.legs import DayFilter, ExpiryChoice, pick_expiry
from tests.optbt.parity.market import build

HOLIDAY_EXPIRY = date(2026, 1, 15)


@pytest.fixture(scope="module")
def history() -> Iterator[History]:
    conn: duckdb.DuckDBPyConnection = build()
    yield History(conn)
    conn.close()


def _view(history: History, day: date) -> View:
    return View(history, day, history.index_day(day))


def _sessions(history: History, day: date, expiry: date) -> int:
    return _view(history, day).sessions_to(expiry)


@pytest.mark.parametrize(
    ("day", "expiry", "left"),
    [
        (date(2026, 1, 8), date(2026, 1, 8), 0),  # the expiry session itself
        (date(2026, 1, 7), date(2026, 1, 8), 1),
        (date(2026, 1, 5), date(2026, 1, 8), 3),
        # Listed on a holiday: it settles on the 14th, which is its session 0.
        (date(2026, 1, 14), HOLIDAY_EXPIRY, 0),
        (date(2026, 1, 13), HOLIDAY_EXPIRY, 1),
        # Across a weekend: Friday is the session before a Monday-side expiry run.
        (date(2026, 1, 9), HOLIDAY_EXPIRY, 3),
    ],
)
def test_sessions_to_expiry_count_trading_days_not_calendar_days(
    history: History, day: date, expiry: date, left: int
) -> None:
    assert _sessions(history, day, expiry) == left


def test_past_the_end_of_the_data_weekdays_stand_in_for_sessions(history: History) -> None:
    # Data ends Fri 23 Jan; the 29th is a Thursday: Mon-Thu after the 23rd is 4.
    assert _sessions(history, date(2026, 1, 23), date(2026, 1, 29)) == 4


@pytest.mark.parametrize(("left", "trades"), [(0, False), (1, False), (2, True), (5, True)])
def test_skip_eve_keeps_off_the_expiry_session_and_the_one_before(left: int, trades: bool) -> None:
    why = DayFilter(expiry_day="skip_eve").why_not({"sessions_to_expiry": left})
    assert (why is None) is trades


def test_skip_eve_does_not_trade_when_the_distance_is_unknown() -> None:
    assert DayFilter(expiry_day="skip_eve").why_not({}) is not None


# Weeklies 8, 15 (a holiday, settling the 14th), 22, 29 Jan; monthly 29 Jan.
@pytest.mark.parametrize(
    ("day", "choice", "picked"),
    [
        # Nearest weekly, as "this week" always was.
        (date(2026, 1, 8), ExpiryChoice(), date(2026, 1, 8)),
        # Not on its own day: the next one.
        (date(2026, 1, 8), ExpiryChoice(min_left=1), date(2026, 1, 15)),
        (date(2026, 1, 7), ExpiryChoice(min_left=1), date(2026, 1, 8)),
        # Not on the day before either.
        (date(2026, 1, 7), ExpiryChoice(min_left=2), date(2026, 1, 15)),
        # The holiday weekly settles on the 14th, so the 14th is its expiry day.
        (date(2026, 1, 14), ExpiryChoice(min_left=1), date(2026, 1, 22)),
        # The 2nd counts from what is left, not from the calendar.
        (date(2026, 1, 8), ExpiryChoice(nth=2), date(2026, 1, 15)),
        (date(2026, 1, 8), ExpiryChoice(nth=2, min_left=1), date(2026, 1, 22)),
        (date(2026, 1, 20), ExpiryChoice("monthly"), date(2026, 1, 29)),
    ],
)
def test_the_expiry_picked_passes_over_any_too_close(
    history: History, day: date, choice: ExpiryChoice, picked: date
) -> None:
    assert pick_expiry(_view(history, day), choice) == picked


def test_no_expiry_qualifying_is_no_expiry(history: History) -> None:
    # The data lists two monthlies; there is no 3rd.
    assert pick_expiry(_view(history, date(2026, 1, 5)), ExpiryChoice("monthly", nth=3)) is None

"""Distance to expiry in sessions, and the filter that keeps clear of its last two."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import duckdb
import pytest

from optbt.data.history import History
from optbt.strategies.legs import DayFilter
from tests.optbt.parity.market import build

HOLIDAY_EXPIRY = date(2026, 1, 15)


@pytest.fixture(scope="module")
def history() -> Iterator[History]:
    conn: duckdb.DuckDBPyConnection = build()
    yield History(conn)
    conn.close()


def _sessions(history: History, day: date, expiry: date) -> int:
    from optbt.market import View

    return View(history, day, history.index_day(day)).sessions_to(expiry)


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

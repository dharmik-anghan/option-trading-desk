from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from venues.calendar import IST, in_session, next_open, session_bounds
from venues.calendar import NSE_CLOSE as CLOSE
from venues.calendar import NSE_OPEN as OPEN


def ist(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=IST)


class TestHours:
    # 30 Sep 2026 is a Wednesday
    @pytest.mark.parametrize(
        ("hh", "mm", "open_"),
        [
            (8, 0, False),   # before anything
            (9, 0, False),   # pre-open: orders collected, nothing trades
            (9, 14, False),
            (9, 15, True),   # the bell
            (12, 0, True),
            (15, 30, True),  # the close itself still counts
            (15, 31, False),
            (20, 0, False),
        ],
    )
    def test_the_continuous_session_only(self, hh: int, mm: int, open_: bool) -> None:
        assert in_session(ist(2026, 9, 30, hh, mm)) is open_

    def test_weekends_are_shut_whatever_the_hour(self) -> None:
        # 3 and 4 Oct 2026 are Saturday and Sunday
        assert in_session(ist(2026, 10, 3, 12, 0)) is False
        assert in_session(ist(2026, 10, 4, 12, 0)) is False

    def test_a_listed_holiday_is_shut(self) -> None:
        # Gandhi Jayanti, a Friday
        holiday = frozenset({date(2026, 10, 2)})
        assert in_session(ist(2026, 10, 2, 12, 0)) is True  # without the list
        assert in_session(ist(2026, 10, 2, 12, 0), holiday) is False


class TestTimezones:
    def test_utc_is_converted_not_compared(self) -> None:
        # 06:45 UTC is 12:15 IST, comfortably inside the session
        assert in_session(datetime(2026, 9, 30, 6, 45, tzinfo=UTC)) is True
        # 16:00 UTC is 21:30 IST, well after the close
        assert in_session(datetime(2026, 9, 30, 16, 0, tzinfo=UTC)) is False

    def test_another_offset_is_handled(self) -> None:
        ny = timezone(timedelta(hours=-4))
        # 02:45 in New York is 12:15 IST the same day
        assert in_session(datetime(2026, 9, 30, 2, 45, tzinfo=ny)) is True

    def test_a_naive_time_is_read_as_ist(self) -> None:
        assert in_session(datetime(2026, 9, 30, 12, 0)) is True
        assert in_session(datetime(2026, 9, 30, 20, 0)) is False

    def test_utc_late_evening_rolls_into_the_next_ist_day(self) -> None:
        # 20:00 UTC Friday is 01:30 IST Saturday - a weekend, not a Friday night
        assert in_session(datetime(2026, 10, 2, 20, 0, tzinfo=UTC)) is False


def test_session_bounds_are_the_bell_times() -> None:
    start, end = session_bounds(date(2026, 9, 30))
    assert start.time() == OPEN
    assert end.time() == CLOSE
    assert start.tzinfo == IST


class TestNextOpen:
    """When the desk should start asking the broker again."""

    def test_a_saturday_waits_for_monday(self) -> None:
        saturday = datetime(2026, 10, 3, 11, 0, tzinfo=IST)
        assert next_open(saturday) == datetime(2026, 10, 5, 9, 15, tzinfo=IST)

    def test_a_holiday_is_skipped(self) -> None:
        # Gandhi Jayanti on a Friday: Thursday evening waits for Monday.
        thursday_evening = datetime(2026, 10, 1, 18, 0, tzinfo=IST)
        holidays = frozenset({date(2026, 10, 2)})
        assert next_open(thursday_evening, holidays) == datetime(2026, 10, 5, 9, 15, tzinfo=IST)

    def test_before_the_bell_is_the_same_morning(self) -> None:
        early = datetime(2026, 10, 5, 8, 0, tzinfo=IST)
        assert next_open(early) == datetime(2026, 10, 5, 9, 15, tzinfo=IST)

    def test_in_session_is_now(self) -> None:
        now = datetime(2026, 10, 5, 11, 0, tzinfo=IST)
        assert next_open(now) == now

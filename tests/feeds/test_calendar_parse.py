from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from feeds.calendar_parse import parse_calendar
from feeds.models import Event

FIXTURE = Path(__file__).parent / "fixtures" / "calendar.html"


@pytest.fixture(scope="module")
def events() -> list[Event]:
    return parse_calendar(FIXTURE.read_text(encoding="utf-8"))


def _named(events: list[Event], name: str) -> Event:
    return next(e for e in events if e.name == name)


def test_reads_the_rows_it_understands_and_skips_the_rest(events: list[Event]) -> None:
    # six well-formed entries, of which two are the same release twice
    names = sorted({e.name for e in events})
    assert names == [
        "Corporate Bond Issuance",
        "House Price Index",
        "Inflation",
        "NREGA Demand",
        "RBI Policy Rate",
    ]


def test_the_reminder_control_is_not_part_of_the_name(events: list[Event]) -> None:
    assert all("Remind" not in e.name for e in events)


def test_a_trailing_country_is_split_out(events: list[Event]) -> None:
    house = _named(events, "House Price Index")
    assert house.country == "Philippines"
    assert house.label == "House Price Index (Philippines)"

    rbi = _named(events, "RBI Policy Rate")
    assert rbi.country is None
    assert rbi.label == "RBI Policy Rate"


def test_coverage_and_importance_come_from_the_data_tag(events: list[Event]) -> None:
    rbi = _named(events, "RBI Policy Rate")
    assert (rbi.coverage, rbi.importance) == ("india", "H")

    us = _named(events, "Inflation")
    assert (us.coverage, us.importance, us.country) == ("global", "H", "United States")

    nrega = _named(events, "NREGA Demand")
    assert (nrega.coverage, nrega.importance) == ("india", "L")


def test_dates_are_parsed_and_the_weekday_prefix_discarded(events: list[Event]) -> None:
    assert _named(events, "RBI Policy Rate").day == date(2026, 10, 7)
    assert _named(events, "House Price Index").day == date(2026, 9, 25)


def test_the_same_release_listed_twice_appears_once(events: list[Event]) -> None:
    # the page lists Corporate Bond Issuance three times on 28 Sep, differing
    # only in value columns this parser does not read
    same = [e for e in events if e.name == "Corporate Bond Issuance"]
    assert len(same) == 1


def test_rows_it_cannot_read_are_dropped_not_guessed(events: list[Event]) -> None:
    assert not any(e.name == "Unknown tag shape" for e in events)
    assert not any(e.name == "Unparseable date" for e in events)
    # a row whose name cell is blank carries nothing worth showing
    assert all(e.name.strip() for e in events)


def test_events_come_back_in_date_order(events: list[Event]) -> None:
    assert [e.day for e in events] == sorted(e.day for e in events)


@pytest.mark.parametrize("page", ["", "not html", "<html><body>no rows</body></html>"])
def test_an_unreadable_page_yields_nothing_rather_than_raising(page: str) -> None:
    # what a markup change looks like: the caller treats empty as "stale", so
    # this must not raise
    assert parse_calendar(page) == []

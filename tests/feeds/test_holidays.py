from __future__ import annotations

from datetime import date

from feeds.holidays import Holidays, parse_holidays

PAYLOAD: dict[str, object] = {
    "CM": [{"tradingDate": "26-Jan-2026", "description": "Republic Day"}],
    "FO": [
        {"tradingDate": "02-Oct-2026", "weekDay": "Friday", "description": "Gandhi Jayanti"},
        {"tradingDate": "20-Oct-2026", "weekDay": "Tuesday", "description": "Dussehra"},
        {"tradingDate": "not a date", "description": "malformed"},
        {"description": "no date field at all"},
        "not even a row",
    ],
}


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_reads_the_derivatives_segment() -> None:
    assert parse_holidays(PAYLOAD) == frozenset({date(2026, 10, 2), date(2026, 10, 20)})


def test_a_different_segment_can_be_asked_for() -> None:
    assert parse_holidays(PAYLOAD, "CM") == frozenset({date(2026, 1, 26)})


def test_a_malformed_row_costs_only_itself() -> None:
    # three of the five FO rows are junk; the two real dates still come through
    assert len(parse_holidays(PAYLOAD)) == 2


def test_a_missing_segment_is_empty_not_an_error() -> None:
    assert parse_holidays({"CM": []}, "FO") == frozenset()
    assert parse_holidays({}) == frozenset()


def test_fetched_once_within_the_day() -> None:
    calls: list[str] = []
    clock = Clock()

    def get(url: str) -> dict[str, object]:
        calls.append(url)
        return PAYLOAD

    holidays = Holidays(now=clock, get=get)
    holidays.dates()
    clock.t += 3600
    holidays.dates()

    assert len(calls) == 1


def test_a_failure_leaves_the_set_empty_rather_than_raising() -> None:
    def boom(_url: str) -> dict[str, object]:
        raise OSError("nseindia unreachable")

    # an unknown holiday list means every weekday is treated as a trading day,
    # which is over-inclusive and never wrong the other way
    assert Holidays(now=Clock(), get=boom).dates() == frozenset()


def test_a_later_failure_keeps_what_was_already_known() -> None:
    state = {"fail": False}
    clock = Clock()

    def flaky(_url: str) -> dict[str, object]:
        if state["fail"]:
            raise OSError("down")
        return PAYLOAD

    holidays = Holidays(now=clock, get=flaky)
    good = holidays.dates()
    state["fail"] = True
    clock.t += 90_000  # past the day-long cache

    assert holidays.dates() == good

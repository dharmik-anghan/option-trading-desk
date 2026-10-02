"""Keeping NSE's pre-open, which is only served on the day."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from marketdata.nse_preopen import PreOpenDay, parse_api, read_csv
from storage.db import connect, init_schema
from storage.preopen_recorder import PreOpenRecorder, latest_settled_day
from storage.preopen_repo import quotes_for_day, recorded_days, save_day, symbol_history
from venues.calendar import IST

FIXTURES = Path(__file__).parent.parent / "marketdata" / "fixtures"
DAY = date(2026, 9, 29)


@pytest.fixture
def conn() -> sqlite3.Connection:
    db = connect(":memory:")
    init_schema(db)
    return db


@pytest.fixture
def api() -> PreOpenDay:
    return parse_api(json.loads((FIXTURES / "nse_preopen_nifty50.json").read_text()))


@pytest.fixture
def csv() -> PreOpenDay:
    return read_csv(FIXTURES / "MW-Pre-Open-Market-NIFTY 50-29-Sep-2026.csv")


def test_a_day_from_the_api_is_kept_with_its_book_and_index(
    conn: sqlite3.Connection, api: PreOpenDay
) -> None:
    result = save_day(conn, api)
    assert (result.outcome, result.rows) == ("stored", 2)
    [day] = recorded_days(conn)
    assert (day.day, day.source, day.rows) == (DAY, "nse", 2)
    assert day.index is not None and day.index.pct_change == -0.21
    stored = {q.symbol: q for q in quotes_for_day(conn, DAY)}
    original = {q.symbol: q for q in api.quotes}
    assert stored == original
    assert stored["DRREDDY"].book == original["DRREDDY"].book


@pytest.mark.parametrize("source", ["api", "csv"])
def test_saving_the_same_day_again_changes_nothing(
    conn: sqlite3.Connection, source: str, request: pytest.FixtureRequest
) -> None:
    session = request.getfixturevalue(source)
    assert save_day(conn, session).outcome == "stored"
    assert save_day(conn, session).outcome == "unchanged"


def test_the_api_replaces_what_a_file_said(
    conn: sqlite3.Connection, api: PreOpenDay, csv: PreOpenDay
) -> None:
    save_day(conn, csv)
    result = save_day(conn, api)
    assert result.outcome == "updated"
    [day] = recorded_days(conn)
    assert day.source == "nse"
    assert quotes_for_day(conn, DAY)[0].total_buy_qty is not None


def test_a_file_does_not_overwrite_the_api(
    conn: sqlite3.Connection, api: PreOpenDay, csv: PreOpenDay
) -> None:
    save_day(conn, api)
    assert save_day(conn, csv).outcome == "unchanged"
    assert recorded_days(conn)[0].source == "nse"
    assert quotes_for_day(conn, DAY)[0].book


def test_a_file_that_disagrees_is_a_conflict_unless_told_to_replace(
    conn: sqlite3.Connection, csv: PreOpenDay
) -> None:
    save_day(conn, csv)
    moved = replace(csv, quotes=tuple(replace(q, final_price=q.final_price + 1)
                                      for q in csv.quotes))
    assert save_day(conn, moved).outcome == "conflict"
    assert save_day(conn, moved, replace=True).outcome == "updated"
    assert quotes_for_day(conn, DAY)[0].final_price == moved.quotes[0].final_price


def test_a_file_can_add_stocks_to_a_known_day(
    conn: sqlite3.Connection, csv: PreOpenDay
) -> None:
    first, second = csv.quotes
    save_day(conn, replace(csv, quotes=(first,)))
    result = save_day(conn, replace(csv, quotes=(first, second)))
    assert (result.outcome, result.rows) == ("added", 1)
    assert recorded_days(conn)[0].rows == 2


def test_a_file_under_the_wrong_date_is_a_duplicate(
    conn: sqlite3.Connection, csv: PreOpenDay
) -> None:
    """The page saved the 23rd's figures as `...-22-Sep-2026 (1).csv`."""
    save_day(conn, csv)
    result = save_day(conn, replace(csv, day=date(2026, 9, 28)))
    assert result.outcome == "duplicate"
    assert "2026-09-29" in result.detail
    assert len(recorded_days(conn)) == 1


def test_an_auction_still_collecting_orders_is_not_kept(
    conn: sqlite3.Connection, api: PreOpenDay
) -> None:
    early = replace(api, as_of=datetime(2026, 9, 29, 9, 5, tzinfo=IST))
    assert save_day(conn, early).outcome == "unsettled"
    assert recorded_days(conn) == []


def test_one_stock_across_days(conn: sqlite3.Connection, csv: PreOpenDay) -> None:
    save_day(conn, csv)
    later = replace(csv, day=date(2026, 9, 30), quotes=tuple(
        replace(q, prev_close=q.final_price) for q in csv.quotes))
    save_day(conn, later)
    history = symbol_history(conn, "DRREDDY")
    assert [d for d, _ in history] == [DAY, date(2026, 9, 30)]


# -- the recorder ----------------------------------------------------------


def _ist(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=IST)


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (_ist(2026, 9, 29, 9, 30), date(2026, 9, 29)),   # Tuesday, settled
        (_ist(2026, 9, 29, 9, 5), date(2026, 9, 28)),    # still collecting
        (_ist(2026, 9, 28, 8, 0), date(2026, 9, 25)),    # Monday early: Friday
        (_ist(2026, 9, 27, 12, 0), date(2026, 9, 25)),   # Sunday
    ],
)
def test_the_day_to_expect(at: datetime, expected: date) -> None:
    assert latest_settled_day(at) == expected


def test_holidays_are_skipped() -> None:
    at = _ist(2026, 10, 2, 10, 0)
    assert latest_settled_day(at, frozenset({date(2026, 10, 2)})) == date(2026, 10, 1)


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    """A file, not memory: the recorder opens its own connection on a thread."""
    path = tmp_path / "trading.db"
    init_schema(connect(str(path)))
    return path


def test_the_recorder_asks_once_per_day(
    db_file: Path, api: PreOpenDay
) -> None:
    calls: list[tuple[str, ...]] = []

    def fetch(keys: tuple[str, ...]) -> PreOpenDay:
        calls.append(keys)
        return api

    recorder = PreOpenRecorder(
        fetch=fetch,
        open_conn=lambda: connect(str(db_file)),
        now=lambda: datetime(2026, 9, 29, 5, 0, tzinfo=UTC),  # 10:30 IST
    )
    assert asyncio.run(recorder.tick()) is True
    assert asyncio.run(recorder.tick()) is False
    assert len(calls) == 1
    assert recorder.last_day == DAY


def test_the_recorder_tries_again_while_nse_shows_the_old_day(
    db_file: Path, api: PreOpenDay
) -> None:
    yesterday = replace(api, day=date(2026, 9, 28))
    answers = [yesterday, api]
    recorder = PreOpenRecorder(
        fetch=lambda keys: answers.pop(0),
        open_conn=lambda: connect(str(db_file)),
        now=lambda: _ist(2026, 9, 29, 9, 9),
    )
    assert asyncio.run(recorder.tick()) is True      # the 28th, which was missing anyway
    assert asyncio.run(recorder.tick()) is True      # and then the 29th
    assert {d.day for d in recorded_days(connect(str(db_file)))} == {date(2026, 9, 28), DAY}

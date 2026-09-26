"""The bar store.

The behaviour worth pinning is replacement: the most recent bar of a live series is
incomplete, so the same candle fetched twice must be one row with the later values
rather than two rows or a stale first answer.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from marketdata.models import Bar, Interval, Series
from marketdata.store import BarStore

BTC = Series(source="yahoo", symbol="BTC-USD", interval=Interval.H1)
GOLD = Series(source="yahoo", symbol="GC=F", interval=Interval.H1)


@pytest.fixture
def store() -> Iterator[BarStore]:
    s = BarStore(":memory:")
    yield s
    s.close()


def bar(minutes: int, close: float = 100.0) -> Bar:
    at = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=minutes)
    return Bar(ts=at, open=99.0, high=101.0, low=98.0, close=close, volume=10.0)


class TestWritingAndReading:
    def test_an_empty_store_reads_as_empty(self, store: BarStore) -> None:
        assert store.read(BTC) == []
        assert store.span(BTC) is None
        assert store.count(BTC) == 0

    def test_bars_come_back_oldest_first(self, store: BarStore) -> None:
        store.write(BTC, [bar(120), bar(0), bar(60)])
        out = store.read(BTC)
        assert [b.ts for b in out] == sorted(b.ts for b in out)
        assert len(out) == 3

    def test_values_survive_the_round_trip(self, store: BarStore) -> None:
        store.write(BTC, [bar(0, close=84_012.5)])
        (back,) = store.read(BTC)
        assert back.close == 84_012.5
        assert back.high == 101.0
        assert back.ts.tzinfo is not None

    def test_writing_nothing_is_not_an_error(self, store: BarStore) -> None:
        assert store.write(BTC, []) == 0


class TestReplacement:
    def test_the_same_bar_written_twice_is_one_row(self, store: BarStore) -> None:
        store.write(BTC, [bar(0, close=100.0)])
        store.write(BTC, [bar(0, close=105.0)])
        assert store.count(BTC) == 1

    def test_the_later_answer_wins(self, store: BarStore) -> None:
        # The newest bar of a live series is incomplete. Fetched again an hour
        # later it has a different high, low and close, and that one is true.
        store.write(BTC, [bar(0, close=100.0)])
        store.write(BTC, [bar(0, close=105.0)])
        (back,) = store.read(BTC)
        assert back.close == 105.0


class TestSeriesAreKeptApart:
    def test_two_symbols_do_not_mix(self, store: BarStore) -> None:
        store.write(BTC, [bar(0, close=84_000.0)])
        store.write(GOLD, [bar(0, close=4_300.0)])
        assert [b.close for b in store.read(BTC)] == [84_000.0]
        assert [b.close for b in store.read(GOLD)] == [4_300.0]

    def test_two_intervals_of_one_symbol_do_not_mix(self, store: BarStore) -> None:
        daily = Series(source="yahoo", symbol="BTC-USD", interval=Interval.D1)
        store.write(BTC, [bar(0)])
        store.write(daily, [bar(0), bar(60)])
        assert store.count(BTC) == 1
        assert store.count(daily) == 2

    def test_two_sources_do_not_mix(self, store: BarStore) -> None:
        # The important one. Yahoo's gold is a dated futures contract and Shark's
        # is a perpetual; merging them would invent a series that never traded.
        shark = Series(source="shark", symbol="XAUUSDT", interval=Interval.H1)
        yahoo = Series(source="yahoo", symbol="GC=F", interval=Interval.H1)
        store.write(shark, [bar(0, close=4_285.0)])
        store.write(yahoo, [bar(0, close=4_321.0)])
        assert [b.close for b in store.read(shark)] == [4_285.0]
        assert [b.close for b in store.read(yahoo)] == [4_321.0]


class TestWindows:
    def test_a_window_trims_both_ends(self, store: BarStore) -> None:
        store.write(BTC, [bar(m) for m in (0, 60, 120, 180)])
        start = datetime(2026, 9, 1, 1, tzinfo=UTC)
        end = datetime(2026, 9, 1, 2, tzinfo=UTC)
        assert len(store.read(BTC, start, end)) == 2

    def test_the_span_reports_the_edges(self, store: BarStore) -> None:
        store.write(BTC, [bar(m) for m in (0, 60, 120)])
        span = store.span(BTC)
        assert span is not None
        first, last = span
        assert first == datetime(2026, 9, 1, tzinfo=UTC)
        assert last == datetime(2026, 9, 1, 2, tzinfo=UTC)


class TestFetchNotes:
    def test_nothing_recorded_to_begin_with(self, store: BarStore) -> None:
        assert store.last_fetch(BTC) is None

    def test_a_failure_is_recorded_as_one(self, store: BarStore) -> None:
        # What stops a rate-limited source being asked again immediately.
        store.note_fetch(BTC, ok=False, note="429 Too Many Requests")
        noted = store.last_fetch(BTC)
        assert noted is not None
        _at, ok, note = noted
        assert ok is False
        assert "429" in note

    def test_the_latest_note_replaces_the_last(self, store: BarStore) -> None:
        store.note_fetch(BTC, ok=False, note="429")
        store.note_fetch(BTC, ok=True, note="120 bars")
        noted = store.last_fetch(BTC)
        assert noted is not None and noted[1] is True


def test_it_survives_being_closed_and_reopened(tmp_path: Path) -> None:
    path = tmp_path / "bars.duckdb"
    first = BarStore(path)
    first.write(BTC, [bar(0, close=84_000.0)])
    first.close()

    second = BarStore(path)
    try:
        assert [b.close for b in second.read(BTC)] == [84_000.0]
    finally:
        second.close()


def test_it_lists_what_it_holds(store: BarStore) -> None:
    store.write(BTC, [bar(0), bar(60)])
    store.write(GOLD, [bar(0)])
    held = dict(store.series_held())
    assert held[BTC] == 2
    assert held[GOLD] == 1

"""The paging walk, against a source that serves a known three years."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from marketdata.backfill import MAX_REFUSALS, Progress, backfill, resume_from
from marketdata.models import Bar, Interval, Series
from marketdata.store import BarStore
from marketdata.yahoo import RateLimited

SERIES = Series(source="binance", symbol="BTCUSDT", interval=Interval.M5)
BEGAN = datetime(2026, 1, 1, tzinfo=UTC)
NOW = BEGAN + timedelta(days=10)


class PagedFake:
    """A source holding bars from `BEGAN` to `NOW`, served a page at a time."""

    def __init__(self, page: int = 1000, refuse_first: int = 0) -> None:
        self.page = page
        self.refuse_first = refuse_first
        self.calls: list[datetime] = []

    def fetch_from(self, symbol: str, interval: Interval, start: datetime) -> list[Bar]:
        self.calls.append(start)
        if self.refuse_first > 0:
            self.refuse_first -= 1
            raise RateLimited("not now")
        step = timedelta(seconds=interval.seconds)
        at = max(start, BEGAN)
        # Align to the grid, the way a venue's bars are.
        offset = (at - BEGAN) % step
        if offset:
            at += step - offset
        out: list[Bar] = []
        while at < NOW and len(out) < self.page:
            out.append(Bar(ts=at, open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0))
            at += step
        return out


def _walk(store: BarStore, source: PagedFake, start: datetime) -> Iterator[Progress]:
    """The backfill, unexhausted - so a test can stop it part way."""
    return backfill(store, source, SERIES, "BTCUSDT", start, until=NOW, sleep=lambda _: None)


def _run(store: BarStore, source: PagedFake, start: datetime) -> list[Progress]:
    return list(_walk(store, source, start))


def test_pages_until_the_series_runs_out() -> None:
    store = BarStore()
    source = PagedFake()

    progress = _run(store, source, BEGAN)

    # ten days of five-minute bars, in pages of a thousand
    assert store.count(SERIES) == 10 * 288
    assert len(progress) == 3
    assert progress[-1].written == 10 * 288


def test_no_bar_is_fetched_twice() -> None:
    """Each page starts after the last bar of the one before it."""
    store = BarStore()
    source = PagedFake()

    _run(store, source, BEGAN)

    assert source.calls == sorted(source.calls)
    assert len(set(source.calls)) == len(source.calls)


def test_running_it_again_changes_nothing() -> None:
    """The store keys on the timestamp, so an overlap replaces rather than doubles."""
    store = BarStore()
    _run(store, PagedFake(), BEGAN)
    first = store.count(SERIES)

    _run(store, PagedFake(), BEGAN)

    assert store.count(SERIES) == first


def test_an_interrupted_run_keeps_what_it_fetched() -> None:
    """The property the script's promise rests on: pages are written as they land."""
    store = BarStore()
    source = PagedFake()

    for _ in _walk(store, source, BEGAN):
        break  # as if the operator pressed ctrl-c after one page

    assert store.count(SERIES) == 1000


def test_it_carries_on_from_what_is_held() -> None:
    store = BarStore()
    source = PagedFake()
    for _ in _walk(store, source, BEGAN):
        break

    resumed = resume_from(store, SERIES, BEGAN)

    # the newest bar held, not the start of the window
    assert resumed == BEGAN + timedelta(minutes=5 * 999)


def test_it_refetches_the_window_when_the_store_starts_later() -> None:
    """Asking for more history than is held is a reason to go back, not forward."""
    store = BarStore()
    _run(store, PagedFake(), BEGAN)

    assert resume_from(store, SERIES, BEGAN - timedelta(days=30)) == BEGAN - timedelta(days=30)


def test_a_rate_limit_is_waited_out_rather_than_fatal() -> None:
    store = BarStore()
    source = PagedFake(refuse_first=2)
    waits: list[float] = []

    list(
        backfill(
            store, source, SERIES, "BTCUSDT", BEGAN, until=NOW, sleep=waits.append
        )
    )

    assert store.count(SERIES) == 10 * 288
    assert 60.0 in waits


def test_it_gives_up_after_a_run_of_refusals() -> None:
    """A block that is not going to lift should not leave a script running all night."""
    store = BarStore()
    source = PagedFake(refuse_first=MAX_REFUSALS + 3)

    list(
        backfill(store, source, SERIES, "BTCUSDT", BEGAN, until=NOW, sleep=lambda _: None)
    )

    assert store.count(SERIES) == 0
    assert len(source.calls) == MAX_REFUSALS


def test_a_source_stuck_on_one_bar_does_not_loop_forever() -> None:
    """A page that never advances would otherwise ask the same question for ever."""

    class Stuck:
        def fetch_from(self, symbol: str, interval: Interval, start: datetime) -> list[Bar]:
            return [Bar(ts=BEGAN, open=1.0, high=1.0, low=1.0, close=1.0, volume=0.0)]

    store = BarStore()

    progress = list(
        backfill(store, Stuck(), SERIES, "BTCUSDT", NOW - timedelta(days=1), until=NOW,
                 sleep=lambda _: None)
    )

    assert len(progress) == 1

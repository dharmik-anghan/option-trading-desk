"""Opening the bar store when it can be opened, rather than once at startup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from marketdata import BarService, BarStore
from marketdata.holder import RETRY_AFTER, BarStoreHolder

START = datetime(2026, 1, 1, tzinfo=UTC)


class Locked:
    """An opener that refuses until told otherwise.

    Injected rather than holding the real file, because DuckDB's lock is between
    processes and not within one: a second connection from this process would
    simply succeed, and the retry under test would never run.
    """

    def __init__(self, *, held: bool = True) -> None:
        self.held = held
        self.attempts = 0

    def __call__(self, path: Path) -> BarStore:
        self.attempts += 1
        if self.held:
            raise OSError("held by another process")
        return BarStore(path)


class Clock:
    def __init__(self) -> None:
        self.at = START

    def __call__(self) -> datetime:
        return self.at


def test_a_store_that_opens_is_served(tmp_path: Path) -> None:
    holder = BarStoreHolder(tmp_path / "bars.duckdb")

    assert isinstance(holder.service(), BarService)
    holder.close()


def test_the_same_service_is_returned_rather_than_a_new_one(tmp_path: Path) -> None:
    """A second open would fail against the first: DuckDB allows one writer."""
    holder = BarStoreHolder(tmp_path / "bars.duckdb")

    assert holder.service() is holder.service()
    holder.close()


def test_a_held_file_gives_nothing_rather_than_raising(tmp_path: Path) -> None:
    """None is a working answer - a chart falls back to asking the venue."""
    holder = BarStoreHolder(tmp_path / "bars.duckdb", open_store=Locked())

    assert holder.service() is None


def test_it_tries_again_once_the_file_is_free(tmp_path: Path) -> None:
    """The reason this class exists.

    A desk starting beside a finishing backfill used to answer "the bar store is
    not open" for the rest of its life, with the file unlocked the whole time.
    """
    clock = Clock()
    opener = Locked()
    holder = BarStoreHolder(tmp_path / "bars.duckdb", now=clock, open_store=opener)

    assert holder.service() is None

    opener.held = False
    clock.at += RETRY_AFTER + timedelta(seconds=1)

    assert isinstance(holder.service(), BarService)
    holder.close()


def test_it_does_not_retry_on_every_request(tmp_path: Path) -> None:
    """A locked file reopened per request would be a lock contention loop."""
    clock = Clock()
    opener = Locked()
    holder = BarStoreHolder(tmp_path / "bars.duckdb", now=clock, open_store=opener)

    assert holder.service() is None
    opener.held = False
    # Free now, but not enough time has passed to look again.
    clock.at += RETRY_AFTER - timedelta(seconds=1)

    assert holder.service() is None
    assert opener.attempts == 1


def test_sources_are_registered_on_a_store_opened_late(tmp_path: Path) -> None:
    """A store that opened on the second attempt needs the same wiring the first
    would have had, or a chart quietly loses its venue."""
    clock = Clock()
    opener = Locked()
    wired: list[BarService] = []
    holder = BarStoreHolder(
        tmp_path / "bars.duckdb", now=clock, on_open=wired.append, open_store=opener
    )

    assert holder.service() is None
    opener.held = False
    clock.at += RETRY_AFTER * 2

    service = holder.service()
    assert wired == [service]
    holder.close()


def test_a_failure_to_register_does_not_lose_the_store(tmp_path: Path) -> None:
    """A desk without a venue still draws charts from what it has."""

    def explode(service: BarService) -> None:
        raise RuntimeError("no credentials")

    holder = BarStoreHolder(tmp_path / "bars.duckdb", on_open=explode)

    assert isinstance(holder.service(), BarService)
    holder.close()


def test_closing_lets_it_open_again(tmp_path: Path) -> None:
    path = tmp_path / "bars.duckdb"
    clock = Clock()
    holder = BarStoreHolder(path, now=clock)
    assert holder.service() is not None

    holder.close()
    clock.at += RETRY_AFTER * 2

    assert holder.service() is not None
    holder.close()

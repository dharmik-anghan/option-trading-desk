"""The migration mechanism itself, not any particular migration.

Exists because the interesting failures are in the runner: a step that runs
twice, a step that half-applies and reports success, a version that moves
without its step. Those are worth tests even while the list is just a baseline.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator

import pytest

from storage import migrations
from storage.db import connect, init_schema
from storage.migrations import Migration, latest_version, migrate, pending, schema_version


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = connect(":memory:")
    init_schema(connection)
    yield connection
    connection.close()


def test_a_fresh_database_is_at_the_latest_version(conn: sqlite3.Connection) -> None:
    assert schema_version(conn) == latest_version()
    assert pending(conn) == []


def test_migrating_again_does_nothing(conn: sqlite3.Connection) -> None:
    assert migrate(conn) == []


def test_an_untouched_database_starts_at_zero() -> None:
    raw = connect(":memory:")
    assert schema_version(raw) == 0
    raw.close()


def test_steps_run_in_order_and_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[int] = []

    def step(version: int) -> Callable[[sqlite3.Connection], None]:
        def apply(_: sqlite3.Connection) -> None:
            ran.append(version)

        return apply

    # deliberately out of order in the tuple, to prove ordering is by version
    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        (
            Migration(version=2, reason="second", apply=step(2)),
            Migration(version=1, reason="first", apply=step(1)),
            Migration(version=3, reason="third", apply=step(3)),
        ),
    )
    fresh = connect(":memory:")
    assert [m.version for m in migrate(fresh)] == [1, 2, 3]
    assert ran == [1, 2, 3]

    # a second pass is a no-op, and nothing runs twice
    assert migrate(fresh) == []
    assert ran == [1, 2, 3]
    assert schema_version(fresh) == 3
    fresh.close()


def test_a_failing_step_does_not_advance_the_version(monkeypatch: pytest.MonkeyPatch) -> None:
    # The point of the whole design: a half-applied step must be retried, not
    # skipped. Reporting version 2 when step 2 raised would mean every later
    # run assumes a column exists that does not.
    def boom(_: sqlite3.Connection) -> None:
        raise RuntimeError("migration blew up")

    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        (
            Migration(version=1, reason="fine", apply=lambda c: None),
            Migration(version=2, reason="broken", apply=boom),
            Migration(version=3, reason="never reached", apply=lambda c: None),
        ),
    )
    fresh = connect(":memory:")
    with pytest.raises(RuntimeError, match="blew up"):
        migrate(fresh)
    assert schema_version(fresh) == 1
    assert [m.version for m in pending(fresh)] == [2, 3]
    fresh.close()


def test_a_step_that_alters_a_table_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    # A real ALTER, because the baseline is a no-op and would pass anything.
    def add_column(c: sqlite3.Connection) -> None:
        c.execute("ALTER TABLE basket ADD COLUMN venue TEXT")

    fresh = connect(":memory:")
    init_schema(fresh)
    # One past whatever the real list ends at, so this keeps testing the runner
    # as migrations are appended rather than silently becoming a no-op.
    nextup = latest_version() + 1
    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        (*migrations.MIGRATIONS, Migration(version=nextup, reason="adds a column",
                                           apply=add_column)),
    )
    assert [m.version for m in migrate(fresh)] == [nextup]
    columns = {row[1] for row in fresh.execute("PRAGMA table_info(basket)")}
    assert "venue" in columns
    fresh.close()

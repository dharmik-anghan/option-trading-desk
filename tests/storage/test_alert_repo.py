"""Alert state across a restart.

The point of these: the engine's correctness depends on the active set coming
back exactly as it was. If it does not, every still-true condition looks new and
the desk re-announces the whole board - which is what the browser version did on
every refresh before the log and the active set were saved together.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from alerting.models import Alert, Limits, Severity
from storage.alert_repo import (
    append,
    clear_log,
    load_active,
    load_limits,
    load_log,
    mark_delivered,
    save_active,
    save_limits,
    undelivered,
)
from storage.db import connect, init_schema


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = connect(":memory:")
    init_schema(c)
    yield c
    c.close()


def _alert(key: str, at: int, subject: str | None = None) -> Alert:
    return Alert(key=key, severity=Severity.WARN, message=f"{key} happened", at=at, subject=subject)


def test_an_empty_log_reads_as_empty(conn: sqlite3.Connection) -> None:
    assert load_log(conn) == []
    assert load_active(conn) == frozenset()


def test_alerts_come_back_newest_first(conn: sqlite3.Connection) -> None:
    append(conn, [_alert("a", 1000), _alert("b", 3000), _alert("c", 2000)])
    assert [a.key for a in load_log(conn)] == ["b", "c", "a"]


def test_an_alert_survives_intact(conn: sqlite3.Connection) -> None:
    append(conn, [_alert("tested:19", 1700, subject="27 Oct - Iron Condor")])
    (back,) = load_log(conn)
    assert back.key == "tested:19"
    assert back.severity is Severity.WARN
    assert back.subject == "27 Oct - Iron Condor"
    assert back.at == 1700


def test_the_log_respects_its_limit(conn: sqlite3.Connection) -> None:
    append(conn, [_alert(f"k{i}", i) for i in range(10)])
    assert len(load_log(conn, limit=3)) == 3


def test_the_active_set_round_trips(conn: sqlite3.Connection) -> None:
    keys = frozenset({"tested:19", "event:8:2026-10-02:NFP"})
    save_active(conn, keys, now=1000)
    assert load_active(conn) == keys


def test_saving_the_active_set_replaces_it(conn: sqlite3.Connection) -> None:
    save_active(conn, frozenset({"a", "b"}), now=1000)
    save_active(conn, frozenset({"b", "c"}), now=2000)
    assert load_active(conn) == frozenset({"b", "c"})


def test_a_key_that_stays_active_keeps_its_original_since(conn: sqlite3.Connection) -> None:
    # so "how long has this been true" stays answerable across passes
    save_active(conn, frozenset({"a"}), now=1000)
    save_active(conn, frozenset({"a", "b"}), now=5000)
    since = {row[0]: row[1] for row in conn.execute("SELECT key, since FROM alert_active")}
    assert since == {"a": 1000, "b": 5000}


class TestDelivery:
    def test_new_alerts_are_undelivered(self, conn: sqlite3.Connection) -> None:
        append(conn, [_alert("a", 1000)])
        assert [a.key for _id, a in undelivered(conn)] == ["a"]

    def test_undelivered_reads_oldest_first(self, conn: sqlite3.Connection) -> None:
        append(conn, [_alert("late", 5000), _alert("early", 1000)])
        assert [a.key for _id, a in undelivered(conn)] == ["early", "late"]

    def test_marking_delivered_takes_it_out_of_the_queue(self, conn: sqlite3.Connection) -> None:
        append(conn, [_alert("a", 1000), _alert("b", 2000)])
        pending = undelivered(conn)
        mark_delivered(conn, [pending[0][0]], at=9999)
        assert [a.key for _id, a in undelivered(conn)] == ["b"]

    def test_a_delivered_alert_is_still_in_the_log(self, conn: sqlite3.Connection) -> None:
        append(conn, [_alert("a", 1000)])
        mark_delivered(conn, [undelivered(conn)[0][0]], at=9999)
        assert [a.key for a in load_log(conn)] == ["a"]


def test_clearing_forgets_the_active_set_too(conn: sqlite3.Connection) -> None:
    # Keeping it would leave every still-true condition marked as already
    # announced, so nothing re-fires and the panel sits empty while several are
    # live. That was a real bug in the browser version.
    append(conn, [_alert("a", 1000)])
    save_active(conn, frozenset({"a"}), now=1000)
    clear_log(conn)
    assert load_log(conn) == []
    assert load_active(conn) == frozenset()


class TestLimits:
    def test_defaults_when_nothing_is_stored(self, conn: sqlite3.Connection) -> None:
        assert load_limits(conn) == Limits()

    def test_limits_round_trip(self, conn: sqlite3.Connection) -> None:
        mine = Limits(target=20000, daily_loss=10000, max_loss=30000, short_delta=0.25,
                      expiry_days=5)
        save_limits(conn, mine)
        assert load_limits(conn) == mine

    def test_saving_twice_updates_rather_than_duplicates(self, conn: sqlite3.Connection) -> None:
        save_limits(conn, Limits(target=1))
        save_limits(conn, Limits(target=2))
        assert load_limits(conn).target == 2
        assert conn.execute("SELECT count(*) FROM alert_limits").fetchone()[0] == 1

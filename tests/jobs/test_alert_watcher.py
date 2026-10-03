"""The background loop, with no broker, no network and no HTTP.

What is worth testing here is not the arithmetic - `test_rules.py` covers that -
but the loop's promises: state survives a pass, a failed delivery is retried
rather than lost, a bad pass does not kill the loop, and a calendar that has not
answered does not clear the event alerts.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from alerting.models import Alert, Limits
from jobs.alert_watcher import Inputs, Watcher
from storage.alert_repo import load_active, load_log, save_limits, undelivered
from storage.db import connect, init_schema
from tests.alerting.conftest import FakeBasket, FakeEvent, FakeLeg
from tests.alerting.test_rules import FakePosition

L = Limits()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """A real file, because the watcher does its work on a worker thread.

    `asyncio.to_thread` means every pass runs off the event loop, and SQLite
    refuses a connection created on another thread. Production opens a fresh
    connection per pass, which is what makes that safe; an in-memory database
    shared across threads would test something the app never does.
    """
    path = tmp_path / "alerts.db"
    with closing(connect(str(path))) as conn:
        init_schema(conn)
    return path


def _read(db: Path) -> sqlite3.Connection:
    return connect(str(db))


class Recorder:
    """A notifier that records instead of sending."""

    def __init__(self, working: bool = True) -> None:
        self.sent: list[list[Alert]] = []
        self.working = working

    def send_alerts(self, alerts: list[Alert]) -> bool:
        self.sent.append(list(alerts))
        return self.working


def _log(db: Path) -> list[Alert]:
    with closing(_read(db)) as conn:
        return load_log(conn)


def _active(db: Path) -> frozenset[str]:
    with closing(_read(db)) as conn:
        return load_active(conn)


def _undelivered(db: Path) -> list[tuple[int, Alert]]:
    with closing(_read(db)) as conn:
        return undelivered(conn)


def _save_limits(db: Path, limits: Limits) -> None:
    with closing(_read(db)) as conn:
        save_limits(conn, limits)


#: A structure past its own stop - which is what an alert-worthy state looks like
#: now that nothing is measured against the account.
_STOPPED = FakeBasket(id=8, name="27 Oct - Iron Condor", stop_loss=-2000.0, total_pnl=-2500.0)


def _breach() -> Inputs:
    return Inputs(total_pnl=0.0, baskets=[_STOPPED], events=[], events_loaded=True)


def _quiet() -> Inputs:
    return Inputs(total_pnl=0.0, baskets=[], events=[], events_loaded=True)


def _watcher(db: Path, gather, notifier=None, now=1_000_000):  # type: ignore[no-untyped-def]
    clock: dict[str, int] = {"t": now}

    def now_ms() -> int:
        return clock["t"]

    w = Watcher(
        gather=gather,
        open_conn=lambda: connect(str(db)),
        notifier=notifier,
        now_ms=now_ms,
    )
    return w, clock


class TestOnePass:
    def test_a_breach_is_recorded(self, db: Path) -> None:
        w, _ = _watcher(db, _breach)
        outcome = asyncio.run(w.tick())
        assert [a.key for a in outcome.fired] == ["stop:8:-2000"]
        assert [a.key for a in _log(db)] == ["stop:8:-2000"]
        assert _active(db) == frozenset({"stop:8:-2000"})

    def test_a_quiet_book_records_nothing(self, db: Path) -> None:
        w, _ = _watcher(db, _quiet)
        assert asyncio.run(w.tick()).fired == []
        assert _log(db) == []

    def test_the_same_condition_is_not_written_twice(self, db: Path) -> None:
        w, clock = _watcher(db, _breach)
        asyncio.run(w.tick())
        clock["t"] += 60_000
        asyncio.run(w.tick())
        assert len(_log(db)) == 1

    def test_state_survives_a_restart(self, db: Path) -> None:
        first, clock = _watcher(db, _breach)
        asyncio.run(first.tick())
        # a whole new Watcher, as a process restart would give
        second, _ = _watcher(db, _breach, now=clock["t"] + 120_000)
        asyncio.run(second.tick())
        assert len(_log(db)) == 1

    def test_stored_limits_are_used_not_the_defaults(self, db: Path) -> None:
        # The remaining limits are defaults for the per-structure rules; a stored
        # one has to reach the pass rather than the dataclass default being used.
        _save_limits(db, Limits(max_loss=1.0))
        wide = FakeBasket(id=9, name="Wide", max_loss=-500_000.0, legs=[FakeLeg()])
        w, _ = _watcher(db, lambda: Inputs(0.0, [wide], [], True))
        assert [a.key for a in asyncio.run(w.tick()).fired] == ["worst-case:9"]


class TestTheCalendarGuard:
    """An unanswered calendar must not read as "nothing is scheduled"."""

    def _with_event(self, loaded: bool, events: list[FakeEvent]) -> Inputs:
        basket = FakeBasket(id=8, expiry_date="27-10-2026", legs=[FakeLeg()])
        return Inputs(total_pnl=0.0, baskets=[basket], events=events, events_loaded=loaded)

    def test_an_event_fires_once(self, db: Path) -> None:
        event = FakeEvent(day=date(2026, 10, 7))
        w, clock = _watcher(db, lambda: self._with_event(True, [event]))
        asyncio.run(w.tick())
        assert len([a for a in _log(db) if a.key.startswith("event:")]) == 1

    def test_an_empty_calendar_does_not_clear_the_event_keys(self, db: Path) -> None:
        event = FakeEvent(day=date(2026, 10, 7))
        inputs = {"value": self._with_event(True, [event])}
        w, clock = _watcher(db, lambda: inputs["value"])
        asyncio.run(w.tick())
        before = _active(db)

        # the scrape came back with nothing - reachable, but unreadable
        inputs["value"] = self._with_event(True, [])
        asyncio.run(w.tick())
        assert _active(db) == before, "an empty calendar cleared the event keys"

        # and when it recovers, nothing is announced again
        clock["t"] += 30 * 60_000
        inputs["value"] = self._with_event(True, [event])
        asyncio.run(w.tick())
        assert len([a for a in _log(db) if a.key.startswith("event:")]) == 1


class TestDelivery:
    def test_a_fired_alert_is_sent(self, db: Path) -> None:
        recorder = Recorder()
        w, _ = _watcher(db, _breach, recorder)
        asyncio.run(w.tick())
        assert [a.key for a in recorder.sent[0]] == ["stop:8:-2000"]
        assert _undelivered(db) == []

    def test_a_failed_send_is_retried_not_lost(self, db: Path) -> None:
        broken = Recorder(working=False)
        w, clock = _watcher(db, _breach, broken)
        asyncio.run(w.tick())
        assert len(_undelivered(db)) == 1, "a failed send must leave it queued"

        # next pass, Telegram is back
        broken.working = True
        clock["t"] += 60_000
        asyncio.run(w.tick())
        assert _undelivered(db) == []
        assert len(broken.sent) == 2

    def test_nothing_new_sends_nothing(self, db: Path) -> None:
        recorder = Recorder()
        w, clock = _watcher(db, _breach, recorder)
        asyncio.run(w.tick())
        clock["t"] += 60_000
        asyncio.run(w.tick())
        assert len(recorder.sent) == 1


class TestTheLoopSurvivesFailure:
    def test_a_raising_gather_does_not_kill_the_loop(self, db: Path) -> None:
        calls = {"n": 0}

        def flaky() -> Inputs:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("broker down")
            return _breach()

        w, _ = _watcher(db, flaky)

        async def run_briefly() -> None:
            w._interval = 0.01  # noqa: SLF001 - the point is to iterate twice quickly
            task = asyncio.create_task(w.run_forever())
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run_briefly())
        assert calls["n"] >= 2, "the loop stopped after one failure"
        assert [a.key for a in _log(db)] == ["stop:8:-2000"]

    def test_a_failure_is_reported_then_cleared(self, db: Path) -> None:
        state = {"fail": True}

        def flaky() -> Inputs:
            if state["fail"]:
                raise RuntimeError("broker down")
            return _quiet()

        w, _ = _watcher(db, flaky)
        with pytest.raises(RuntimeError):
            asyncio.run(w.tick())
        assert w.last_error is None  # tick() itself does not swallow

        state["fail"] = False
        asyncio.run(w.tick())
        assert w.last_error is None
        assert w.last_run_at is not None


class TestLeveragedPositionsInAPass:
    """Positions reaching the watcher, and the distinction that protects them."""

    def _unprotected(self, loaded: bool = True) -> Inputs:
        held = FakePosition(protected=False, liquidation_distance=0.5)
        return Inputs(
            total_pnl=0.0,
            baskets=[],
            events=[],
            events_loaded=True,
            positions=[held],
            positions_loaded=loaded,
        )

    def test_an_unprotected_position_is_recorded(self, db: Path) -> None:
        w, _ = _watcher(db, self._unprotected)
        assert [a.key for a in asyncio.run(w.tick()).fired] == ["unprotected:p-1"]

    def test_it_is_said_once_not_every_minute(self, db: Path) -> None:
        w, clock = _watcher(db, self._unprotected)
        asyncio.run(w.tick())
        clock["t"] += 60_000
        asyncio.run(w.tick())
        assert len(_log(db)) == 1

    def test_attaching_a_stop_clears_it(self, db: Path) -> None:
        state = {"protected": False}

        def gather() -> Inputs:
            held = FakePosition(protected=state["protected"], liquidation_distance=0.5)
            return Inputs(0.0, [], [], True, positions=[held], positions_loaded=True)

        w, clock = _watcher(db, gather)
        asyncio.run(w.tick())
        assert _active(db) == frozenset({"unprotected:p-1"})

        state["protected"] = True
        clock["t"] += 60_000
        asyncio.run(w.tick())
        assert _active(db) == frozenset()

    def test_a_venue_that_did_not_answer_does_not_clear_the_warning(self, db: Path) -> None:
        # An empty list because a request failed means "we cannot see your
        # positions", not "you have none" - and treating the first as the second
        # would silently clear every warning about them.
        w, clock = _watcher(db, self._unprotected)
        asyncio.run(w.tick())
        before = _active(db)

        def blind() -> Inputs:
            return Inputs(0.0, [], [], True, positions=[], positions_loaded=False)

        w2, _ = _watcher(db, blind, now=clock["t"] + 60_000)
        asyncio.run(w2.tick())
        assert _active(db) == before, "a blind pass cleared a position warning"

    def test_a_venue_reporting_no_positions_does_clear_it(self, db: Path) -> None:
        w, clock = _watcher(db, self._unprotected)
        asyncio.run(w.tick())

        def none_open() -> Inputs:
            return Inputs(0.0, [], [], True, positions=[], positions_loaded=True)

        w2, _ = _watcher(db, none_open, now=clock["t"] + 60_000)
        asyncio.run(w2.tick())
        assert _active(db) == frozenset()

"""The loop that watches the market while nobody is looking at it.

Alerts used to be computed in the browser, which meant nothing was watching when
the tab was closed. That is survivable for index options, which trade six and a
quarter hours on weekdays, and not at all for a market that runs through the
night.

Async because the streaming work that comes next is: one event loop, a task per
long-lived job. The data it reads is fetched by blocking calls - broker SDKs and
SQLite - so those go through `asyncio.to_thread` rather than being awaited
directly, which keeps the loop responsive without pretending the libraries are
async.

Knows nothing about HTTP, brokers or schemas: it is handed a callable that
returns what to judge. That is what makes it testable without a network, and
what will let a second desk feed the same engine.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from alerting.engine import reconcile
from alerting.models import Alert, BasketView, EventView, Limits, Outcome
from alerting.rules import evaluate
from storage.alert_repo import (
    append,
    load_active,
    load_limits,
    load_log,
    mark_delivered,
    save_active,
    undelivered,
)

log = logging.getLogger(__name__)

#: How often to judge. Slower than the UI polls on purpose: this exists to
#: notice a situation, not to animate a number, and every pass costs broker
#: requests against a per-minute budget.
DEFAULT_INTERVAL = 60.0


@dataclass(frozen=True)
class Inputs:
    """Everything one pass judges.

    `events_loaded` is held apart from an empty list because the two mean
    opposite things: a calendar that has not answered cannot be judged, while an
    empty one that has answered means there is genuinely nothing scheduled. Read
    as the same thing, every event alert re-fires whenever a scrape comes back
    empty - which is exactly how six of them arrived three times in a day.
    """

    total_pnl: float | None
    baskets: Sequence[BasketView]
    events: Sequence[EventView]
    events_loaded: bool


class Notifier(Protocol):
    """What the watcher needs of a delivery channel.

    A protocol, so `notify.Telegram` satisfies it without this module importing
    it - and so a test can pass something that records instead of sending.
    """

    def send_alerts(self, alerts: list[Alert]) -> bool:
        """True if they went. False means try again later."""
        ...


def _now_ms() -> int:
    return int(time.time() * 1000)


class Watcher:
    """Judges the book on an interval, records what fired, and delivers it."""

    def __init__(
        self,
        gather: Callable[[], Inputs],
        open_conn: Callable[[], sqlite3.Connection],
        notifier: Notifier | None = None,
        interval: float = DEFAULT_INTERVAL,
        now_ms: Callable[[], int] = _now_ms,
    ) -> None:
        self._gather = gather
        self._open_conn = open_conn
        self._notifier = notifier
        self._interval = interval
        self._now_ms = now_ms
        #: Set when a pass raises, so `/api/alerts` can say the watcher is unwell
        #: rather than silently showing a stale log.
        self.last_error: str | None = None
        self.last_run_at: int | None = None

    async def run_forever(self) -> None:
        """Judge on the interval until cancelled.

        A pass that raises is logged and the loop continues. The alternative -
        letting the task die - means the desk stops watching and says nothing
        about it, which is the worst of the options.
        """
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a bad pass must not end the loop
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("alert pass failed")
            await asyncio.sleep(self._interval)

    async def tick(self) -> Outcome:
        """One pass: gather, judge, record, deliver."""
        inputs = await asyncio.to_thread(self._gather)
        outcome = await asyncio.to_thread(self._judge, inputs)
        if self._notifier is not None:
            await asyncio.to_thread(self._deliver)
        self.last_error = None
        self.last_run_at = self._now_ms()
        return outcome

    def _judge(self, inputs: Inputs) -> Outcome:
        conn = self._open_conn()
        try:
            active = load_active(conn)
            limits: Limits = load_limits(conn)
            now = self._now_ms()
            conditions = evaluate(
                inputs.total_pnl,
                list(inputs.baskets),
                limits,
                list(inputs.events),
                sticky=active,
            )
            # An event key cannot be judged until the calendar has answered with
            # something. See Inputs.events_loaded.
            usable = inputs.events_loaded and bool(inputs.events)

            def evaluable(key: str) -> bool:
                return usable if key.startswith("event:") else True

            outcome = reconcile(
                active,
                load_log(conn),
                conditions,
                now,
                evaluable=evaluable,
            )
            append(conn, outcome.fired)
            save_active(conn, outcome.active, now)
            return outcome
        finally:
            conn.close()

    def _deliver(self) -> None:
        """Send whatever has not been sent, and only mark what actually went.

        An alert stays undelivered until a send succeeds, so an unreachable
        Telegram means a late message rather than a lost one.
        """
        assert self._notifier is not None
        conn = self._open_conn()
        try:
            pending = undelivered(conn)
            if not pending:
                return
            if self._notifier.send_alerts([alert for _row_id, alert in pending]):
                mark_delivered(conn, [row_id for row_id, _alert in pending], self._now_ms())
        finally:
            conn.close()

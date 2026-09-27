"""Writing down what options cost, on a loop, while the market is open.

Its own task rather than a passenger on the alert watcher, which has one job,
and rather than a passenger on the chain endpoint, which only runs when somebody
has that panel open. This has to run with the tab closed - that is the whole
point of it, and it is the same reason the alert engine moved to the backend.

Cheap by construction. It asks for a handful of strikes rather than a chain,
because only the money matters here, and it writes one row per underlying per
day which the next pass replaces. A session's worth of passes therefore leaves
one row: the last reading before the close.

Nothing is recorded while the exchange is shut. There is nothing to record -
the chain does not move overnight - and a row written at midnight would be
yesterday's close wearing today's date.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from analytics.vol_snapshot import snapshot_from
from broker.models import OptionChain
from storage.vol_repo import save_vol_snapshot

log = logging.getLogger(__name__)

#: Between passes. Fifteen minutes is about twenty-five readings a session per
#: underlying, each one replacing the last, which is far more often than needed
#: to end up with a good closing figure and far less often than anything that
#: would trouble a rate limit.
DEFAULT_INTERVAL = 900.0

#: Strikes either side of the money to ask for. Two, because the at-the-money
#: pair is all this reads and a whole chain would be a much larger response for
#: nothing.
STRIKES = 2


class VolRecorder:
    """Records the at-the-money implied volatility of each underlying."""

    def __init__(
        self,
        underlyings: Sequence[str],
        fetch_chain: Callable[[str, int], OptionChain],
        open_conn: Callable[[], sqlite3.Connection],
        in_session: Callable[[datetime], bool],
        interval: float = DEFAULT_INTERVAL,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._underlyings = list(underlyings)
        self._fetch = fetch_chain
        self._open_conn = open_conn
        self._in_session = in_session
        self._interval = interval
        self._now = now
        #: Set when a pass raises, so the desk can say this is unwell rather
        #: than quietly recording nothing.
        self.last_error: str | None = None
        self.last_run_at: datetime | None = None
        self.recorded: int = 0

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a bad pass must not end the loop
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("volatility recording failed")
            await asyncio.sleep(self._interval)

    async def tick(self) -> int:
        """One pass. Returns how many underlyings were recorded."""
        now = self._now()
        if not self._in_session(now):
            return 0
        written = await asyncio.to_thread(self._record)
        self.last_run_at = now
        self.recorded += written
        return written

    def _record(self) -> int:
        written = 0
        conn = self._open_conn()
        try:
            for underlying in self._underlyings:
                try:
                    chain = self._fetch(underlying, STRIKES)
                except Exception as exc:  # noqa: BLE001 - one symbol, not the pass
                    log.warning("no chain for %s: %s", underlying, exc)
                    continue
                snapshot = snapshot_from(chain, underlying)
                if snapshot is None:
                    # A chain with no greeks answers no question this table
                    # exists for. A row of nulls would sit in the history
                    # looking like a day volatility was unknown rather than a
                    # day the fetch was broken.
                    continue
                save_vol_snapshot(conn, snapshot)
                written += 1
        finally:
            conn.close()
        if written:
            self.last_error = None
        return written

"""Opening the bar store when it can be opened, rather than once at startup.

DuckDB allows one process to write a database file. That is fine for a desk that
is one process, and it makes the moment of startup fragile: a copy of the app
still shutting down, a backfill script finishing, a script left open in a shell -
any of them holds the file for a few seconds, and a desk that tried once at boot
spends the rest of its life unable to read history.

That is not theoretical. This app has started next to a finishing backfill more
than once, logged one warning, and then answered "the bar store is not open" to
every request for the next several hours - with the file sitting there unlocked
the whole time.

So opening is retried, with a gap so a genuinely busy file is not hammered. The
first caller after the gap gets a store if one can be had.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from marketdata.service import BarService
from marketdata.store import BarStore

log = logging.getLogger(__name__)

#: How long to wait before trying again. Long enough that a locked file is not
#: reopened on every request, short enough that a desk started beside a backfill
#: is working again by the time anyone notices.
RETRY_AFTER = timedelta(seconds=30)


class BarStoreHolder:
    """The bar store, if it can be opened. Tries again if it could not.

    `service()` returns None while the file is held elsewhere, which is what a
    caller has to cope with anyway - the store has always been optional, and a
    chart falls back to asking the venue.
    """

    def __init__(
        self,
        path: Path,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        on_open: Callable[[BarService], None] | None = None,
        open_store: Callable[[Path], BarStore] = BarStore,
    ) -> None:
        self._path = path
        #: How to open it. Injectable because DuckDB's lock is between processes
        #: and not within one, so a test cannot hold the file against itself -
        #: a second connection from the same process simply succeeds, and the
        #: retry this class exists for would never be exercised.
        self._open = open_store
        self._now = now
        #: Called once each time a store is opened, to register sources on it.
        #: A store reopened after a failed start needs the same wiring the first
        #: one would have had, or a chart would silently lose its venue.
        self._on_open = on_open
        self._store: BarStore | None = None
        self._service: BarService | None = None
        self._tried_at: datetime | None = None
        self._lock = threading.Lock()

    @property
    def store(self) -> BarStore | None:
        self.service()
        return self._store

    def service(self) -> BarService | None:
        if self._service is not None:
            return self._service
        with self._lock:
            # Checked again inside the lock: two requests arriving together
            # would otherwise both open the file, and the second would fail
            # against the first.
            if self._service is not None:
                return self._service
            now = self._now()
            if self._tried_at is not None and now - self._tried_at < RETRY_AFTER:
                return None
            self._tried_at = now
            try:
                store = self._open(self._path)
            except Exception:  # noqa: BLE001 - one writer; see the module docstring
                log.warning(
                    "bar store is held elsewhere; will try again in %ds",
                    int(RETRY_AFTER.total_seconds()),
                )
                return None
            service = BarService(store)
            if self._on_open is not None:
                try:
                    self._on_open(service)
                except Exception:  # noqa: BLE001 - a desk without a venue still draws
                    log.warning("could not register a venue as a bar source", exc_info=True)
            self._store = store
            self._service = service
            log.info("bar store open at %s", self._path)
            return service

    def close(self) -> None:
        with self._lock:
            if self._store is not None:
                self._store.close()
            self._store = None
            self._service = None

"""Trading holidays, from the exchange's own list.

Cached for a day: the list changes once a year, and being briefly wrong about
a holiday costs a few duplicate P&L snapshots rather than anything a trader
would notice. A failure leaves the set empty, which degrades to treating every
weekday as a trading day - over-inclusive, never wrong in the other direction.
"""

from __future__ import annotations

import threading
import time as clock
from collections.abc import Callable
from datetime import date, datetime

import requests

HOLIDAYS_URL = "https://www.nseindia.com/api/holiday-master?type=trading"
#: The derivatives segment, since this desk trades options.
SEGMENT = "FO"
TTL = 24 * 3600.0
TIMEOUT = 12.0
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; option-strategy desk)"}


def parse_holidays(payload: dict[str, object], segment: str = SEGMENT) -> frozenset[date]:
    """The dates in one segment of the exchange's holiday list.

    Rows it cannot read are skipped rather than raising: a single malformed
    entry should not cost the whole list.
    """
    rows = payload.get(segment)
    if not isinstance(rows, list):
        return frozenset()
    out: set[date] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = row.get("tradingDate")
        if not isinstance(text, str):
            continue
        try:
            out.add(datetime.strptime(text.strip(), "%d-%b-%Y").date())
        except ValueError:
            continue
    return frozenset(out)


class Holidays:
    """Cached holiday dates, safe to share and to ask often."""

    def __init__(
        self,
        now: Callable[[], float] = clock.monotonic,
        get: Callable[[str], dict[str, object]] = lambda url: _fetch(url),
    ) -> None:
        self._now = now
        self._get = get
        self._lock = threading.Lock()
        self._dates: frozenset[date] = frozenset()
        self._at: float | None = None

    def dates(self) -> frozenset[date]:
        with self._lock:
            if self._at is not None and self._now() - self._at < TTL:
                return self._dates
        try:
            parsed = parse_holidays(self._get(HOLIDAYS_URL))
        except Exception:  # noqa: BLE001 - an unknown holiday list is an empty one
            parsed = frozenset()
        with self._lock:
            if parsed:
                self._dates = parsed
                self._at = self._now()
            elif self._at is None:
                # nothing cached and nothing fetched: retry on the next ask
                # rather than pinning an empty set for a day
                self._dates = frozenset()
            return self._dates


def _fetch(url: str) -> dict[str, object]:
    response = requests.get(url, timeout=TIMEOUT, headers=_HEADERS)
    response.raise_for_status()
    body: dict[str, object] = response.json()
    return body

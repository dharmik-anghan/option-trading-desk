"""Fetching the feeds, with a cache and without ever raising at the caller.

Both feeds are somebody else's website. They will be slow, unreachable, or
differently shaped sooner or later, and none of that should reach the desk as
a failure: a calendar that could not be refreshed is a stale calendar, and a
news source that is down is one fewer source. Each cache therefore keeps its
last good answer and reports how old it is, so the UI can say "as of" rather
than implying the data is current.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import requests

from feeds.calendar_parse import parse_calendar
from feeds.models import Event, Headline
from feeds.news_parse import parse_rss
from feeds.sources import CALENDAR_URL, NEWS_SOURCES

#: The calendar is a schedule months out; refetching it often buys nothing.
CALENDAR_TTL = 6 * 3600.0
NEWS_TTL = 300.0
TIMEOUT = 12.0
#: Some publishers refuse a default client.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; option-strategy desk)"}


@dataclass
class Cached:
    """A feed's last good answer and when it arrived."""

    events: list[Event] = field(default_factory=list)
    headlines: list[Headline] = field(default_factory=list)
    fetched_at: float | None = None
    error: str | None = None


def _get(url: str) -> bytes:
    response = requests.get(url, timeout=TIMEOUT, headers=_HEADERS)
    response.raise_for_status()
    # Bytes, not text: XML carries its own encoding declaration, and letting an
    # HTTP client guess instead silently breaks feeds that omit a charset.
    content: bytes = response.content
    return content


class Feeds:
    """Cached access to the calendar and the news, safe to share and to poll."""

    def __init__(
        self,
        now: Callable[[], float] = time.monotonic,
        get: Callable[[str], bytes] = _get,
    ) -> None:
        self._now = now
        self._get = get
        self._lock = threading.Lock()
        self._calendar = Cached()
        self._news = Cached()

    # ---- calendar ----

    def events(self) -> Cached:
        with self._lock:
            if self._fresh(self._calendar, CALENDAR_TTL):
                return self._calendar
        parsed: list[Event] = []
        error: str | None = None
        try:
            parsed = parse_calendar(self._get(CALENDAR_URL).decode("utf-8", "replace"))
            if not parsed:
                # Reachable but unreadable, which is what a markup change looks
                # like. Say so rather than reporting an empty calendar.
                error = "The calendar page was reachable but no events could be read from it."
        except Exception as exc:  # noqa: BLE001 - any failure is a stale calendar
            error = f"Could not refresh the calendar: {type(exc).__name__}"
        with self._lock:
            if parsed:
                self._calendar = Cached(events=parsed, fetched_at=self._now())
            else:
                self._calendar.error = error
            return self._calendar

    # ---- news ----

    def headlines(self) -> Cached:
        with self._lock:
            if self._fresh(self._news, NEWS_TTL):
                return self._news

        collected: list[Headline] = []
        failed: list[str] = []
        for source in NEWS_SOURCES:
            try:
                collected.extend(parse_rss(self._get(source.url), source.name))
            except Exception:  # noqa: BLE001 - one source down is not an outage
                failed.append(source.name)
        collected.sort(key=_recency, reverse=True)

        with self._lock:
            if collected:
                self._news = Cached(
                    headlines=collected,
                    fetched_at=self._now(),
                    error=None if not failed else f"No headlines from {', '.join(failed)}.",
                )
            else:
                self._news.error = "No news source could be reached."
            return self._news

    def _fresh(self, cached: Cached, ttl: float) -> bool:
        return cached.fetched_at is not None and self._now() - cached.fetched_at < ttl

    def age_seconds(self, cached: Cached) -> float | None:
        return None if cached.fetched_at is None else self._now() - cached.fetched_at


def _recency(headline: Headline) -> float:
    """Numeric, so a missing date and a naive one cannot break the sort."""
    if headline.published is None:
        return 0.0
    try:
        return headline.published.timestamp()
    except (OverflowError, OSError, ValueError):
        return 0.0


def upcoming(events: list[Event], today: date, *, days: int, importance: str = "HM") -> list[Event]:
    """Events from today out to `days` ahead, at the given importance levels."""
    return [
        e
        for e in events
        if e.importance in importance and 0 <= (e.day - today).days <= days
    ]

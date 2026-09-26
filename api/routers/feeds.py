"""The economic calendar and the news feed.

Venue-independent on purpose: a scheduled release moves an index and a gold
perpetual alike, so both desks read one feed rather than each having its own.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter

from api.deps import FeedsDep
from api.schemas import (
    EventResponse,
    EventsResponse,
    HeadlineResponse,
    NewsResponse,
)
from feeds.fetch import upcoming
from feeds.sources import NEWS_SOURCES, Topic

router = APIRouter()


@router.get("/api/events", response_model=EventsResponse)
def events(feeds: FeedsDep, days: int = 45, importance: str = "HM") -> EventsResponse:
    """Scheduled releases from today out to `days` ahead.

    `importance` is a string of the levels to keep, e.g. "H" or "HM". The
    calendar carries several hundred entries, most of them minor, so filtering
    is the default rather than an option.
    """
    cached = feeds.events()
    wanted = upcoming(cached.events, date.today(), days=days, importance=importance.upper())
    return EventsResponse(
        events=[
            EventResponse(
                day=e.day.isoformat(),
                name=e.name,
                label=e.label,
                importance=e.importance,
                coverage=e.coverage,
                country=e.country,
            )
            for e in wanted
        ],
        age_seconds=feeds.age_seconds(cached),
        error=cached.error,
    )

@router.get("/api/news", response_model=NewsResponse)
def news(feeds: FeedsDep, limit: int = 40, topics: str = "") -> NewsResponse:
    """Recent market headlines, newest first, pooled across the feeds.

    `topics` is a comma-separated filter - "crypto,commodities" - and empty means
    everything. Filtered on read rather than on fetch: all the feeds are pulled
    once into one cache, so narrowing the list costs nothing and switching desks
    does not start a round of requests.

    An unknown topic is ignored rather than rejected. The filter is a
    convenience, and a stale bookmark naming a topic that has since been dropped
    should show news rather than a 422.
    """
    wanted = {t.strip().lower() for t in topics.split(",") if t.strip()}
    known = {str(t) for t in Topic}
    wanted &= known

    cached = feeds.headlines()
    matching = [
        h for h in cached.headlines if not wanted or (h.topics & wanted)
    ]
    return NewsResponse(
        headlines=[
            HeadlineResponse(
                title=h.title,
                link=h.link,
                source=h.source,
                published=h.published.isoformat() if h.published else None,
                topics=sorted(h.topics),
            )
            for h in matching[: max(1, limit)]
        ],
        available_topics=sorted(known),
        sources={s.name: sorted(str(t) for t in s.topics) for s in NEWS_SOURCES},
        age_seconds=feeds.age_seconds(cached),
        error=cached.error,
    )

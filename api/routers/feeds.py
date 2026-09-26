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
def news(feeds: FeedsDep, limit: int = 40) -> NewsResponse:
    """Recent market headlines, newest first, pooled across the feeds."""
    cached = feeds.headlines()
    return NewsResponse(
        headlines=[
            HeadlineResponse(
                title=h.title,
                link=h.link,
                source=h.source,
                published=h.published.isoformat() if h.published else None,
            )
            for h in cached.headlines[: max(1, limit)]
        ],
        age_seconds=feeds.age_seconds(cached),
        error=cached.error,
    )

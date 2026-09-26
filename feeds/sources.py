"""Where the calendar and the headlines come from.

Kept in one place so a feed can be swapped or dropped without touching the
fetching or the parsing.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The economic calendar. Server-rendered HTML whose event rows carry
#: `data-date` and a `data-tag` of "coverage|importance" - see
#: `feeds.calendar_parse`. Somebody else's page, so it is fetched once every
#: few hours and a parse that returns nothing is treated as a stale calendar
#: rather than an error.
CALENDAR_URL = "https://zerodha.com/markets/calendar/"


@dataclass(frozen=True)
class NewsSource:
    name: str
    url: str


#: Moneycontrol's market-reports feed is deliberately absent: it was still
#: serving April items in late September, so it would only add stale noise.
NEWS_SOURCES: tuple[NewsSource, ...] = (
    NewsSource("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    NewsSource("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
    NewsSource("Livemint", "https://www.livemint.com/rss/markets"),
    NewsSource("RBI", "https://www.rbi.org.in/pressreleases_rss.xml"),
)

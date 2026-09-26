"""Where the calendar and the headlines come from.

Kept in one place so a feed can be swapped or dropped without touching the
fetching or the parsing.

Sources carry topics because the desk now covers two unrelated markets. An RBI
press release bears on an index position and says nothing about a gold
perpetual; a Bitcoin story is the reverse. Filtering on the source rather than
on the words in a headline is cruder and honest: a publisher's beat is a fact
about the publisher, while guessing a topic from a title is a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Topic(StrEnum):
    """What a source writes about."""

    #: Indian equities, the indices, and the RBI - the options desk's world.
    INDIA = "india"
    #: Crypto.
    CRYPTO = "crypto"
    #: Metals and energy: what the tradfi perpetuals track.
    COMMODITIES = "commodities"


#: The economic calendar. Server-rendered HTML whose event rows carry
#: `data-date` and a `data-tag` of "coverage|importance" - see
#: `feeds.calendar_parse`. Somebody else's page, so it is fetched once every
#: few hours and a parse that returns nothing is treated as a stale calendar
#: rather than an error.
#:
#: Shared by both desks deliberately: a US inflation print moves an index and a
#: gold contract alike, so there is one calendar and no filter on it.
CALENDAR_URL = "https://zerodha.com/markets/calendar/"


@dataclass(frozen=True)
class NewsSource:
    name: str
    url: str
    topics: frozenset[Topic]


#: Moneycontrol's market-reports feed is deliberately absent: it was still
#: serving April items in late September, so it would only add stale noise.
#: Mining.com answers 403 to anything without a browser's fingerprint, and
#: CoinDesk redirects once before serving - fine, since the fetcher follows.
#:
#: Each was checked for freshness before being added, which is the test
#: Moneycontrol failed: the Indian and crypto feeds were minutes old, the
#: commodity ones fifteen hours, which is the pace those markets are written
#: about rather than a stale feed.
NEWS_SOURCES: tuple[NewsSource, ...] = (
    NewsSource(
        "Economic Times",
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        frozenset({Topic.INDIA}),
    ),
    NewsSource(
        "Business Standard",
        "https://www.business-standard.com/rss/markets-106.rss",
        frozenset({Topic.INDIA}),
    ),
    NewsSource("Livemint", "https://www.livemint.com/rss/markets", frozenset({Topic.INDIA})),
    NewsSource("RBI", "https://www.rbi.org.in/pressreleases_rss.xml", frozenset({Topic.INDIA})),
    NewsSource(
        "CoinDesk",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        frozenset({Topic.CRYPTO}),
    ),
    NewsSource("Cointelegraph", "https://cointelegraph.com/rss", frozenset({Topic.CRYPTO})),
    NewsSource("CryptoSlate", "https://cryptoslate.com/feed/", frozenset({Topic.CRYPTO})),
    NewsSource("OilPrice", "https://oilprice.com/rss/main", frozenset({Topic.COMMODITIES})),
    NewsSource(
        "Investing.com",
        "https://www.investing.com/rss/commodities.rss",
        frozenset({Topic.COMMODITIES}),
    ),
)



def sources_for(topics: frozenset[Topic]) -> tuple[NewsSource, ...]:
    """Sources covering any of these topics. Everything when none are named."""
    if not topics:
        return NEWS_SOURCES
    return tuple(s for s in NEWS_SOURCES if s.topics & topics)

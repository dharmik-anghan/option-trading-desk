"""Reading headlines out of an RSS feed.

Parsed with the standard library rather than a feed package: RSS is simple, and
these publishers each bend it slightly differently, so handling the variation
explicitly is clearer than configuring somebody else's parser. Every field is
optional as far as this is concerned - a feed that omits a date still gives
usable headlines.
"""

from __future__ import annotations

import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from feeds.models import Headline

_TAGS = re.compile(r"<[^>]+>")
#: Some publishers leak a CDATA terminator into the text itself.
_STRAY_CDATA = re.compile(r"\]\]>\s*$")


def parse_rss(xml: bytes | str, source: str) -> list[Headline]:
    """Headlines from one feed, newest first where dates allow.

    Takes bytes by preference. An XML document declares its own encoding, and
    guessing instead is how RBI's feed came back empty: it sends no charset
    header, so an HTTP client assumed Latin-1, and the UTF-8 byte-order mark
    decoded into "ï»¿" ahead of the declaration - which is then not
    well-formed XML at all. Handing the raw bytes over lets the declaration be
    honoured.

    Returns an empty list rather than raising on malformed XML: a publisher
    serving a broken feed should cost its own headlines, not the panel.
    """
    payload = xml.strip() if isinstance(xml, bytes) else xml.strip().encode("utf-8")
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return []

    out: list[Headline] = []
    for item in root.iter("item"):
        title = _clean(_child(item, "title"))
        if not title:
            continue
        out.append(
            Headline(
                title=title,
                link=_clean(_child(item, "link")),
                source=source,
                published=_parse_date(_child(item, "pubDate")),
            )
        )
    out.sort(key=_recency, reverse=True)
    return out


def _recency(headline: Headline) -> float:
    """A sortable instant, and 0 when the feed gave no date.

    Deliberately numeric. Sorting on the datetime itself compares None with
    None when two items both lack a date, which raises, and compares naive with
    aware datetimes when publishers disagree about timezones, which also
    raises. Neither should be able to take down a headline list.
    """
    if headline.published is None:
        return 0.0
    try:
        return headline.published.timestamp()
    except (OverflowError, OSError, ValueError):
        return 0.0


def _child(item: ElementTree.Element, tag: str) -> str:
    found = item.find(tag)
    return "" if found is None or found.text is None else found.text


def _clean(text: str) -> str:
    stripped = _STRAY_CDATA.sub("", text)
    return re.sub(r"\s+", " ", _TAGS.sub("", stripped)).strip()


def _parse_date(text: str) -> datetime | None:
    if not text.strip():
        return None
    try:
        return parsedate_to_datetime(text.strip())
    except (TypeError, ValueError):
        return None

"""Reading the economic calendar out of its published HTML.

The page is server-rendered, and each event row carries the two things worth
having as attributes - `data-date` and a `data-tag` of the form
``coverage|importance`` - because the page's own filters run off them. That
makes this a good deal less brittle than reading the visible table would be,
though it is still someone else's markup and can change without warning:
`parse_calendar` returns what it understood and never raises, so a change
degrades the calendar rather than the desk.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime

from feeds.models import Coverage, Event, Importance

_ROW = re.compile(
    r'<tr\s+class="entry"[^>]*?data-tag="(?P<tag>[^"]*)"[^>]*?data-date="(?P<date>[^"]*)"[^>]*?>'
    r"(?P<body>.*?)</tr>",
    re.S,
)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAGS = re.compile(r"<[^>]+>")
#: The page appends its own reminder control to the event name.
_NOISE = re.compile(r"\s*Remind me\s*$", re.I)
#: A trailing parenthesised country, e.g. "Inflation (United States)".
_COUNTRY = re.compile(r"^(?P<name>.*?)\s*\((?P<country>[^()]+)\)\s*$")

_IMPORTANCE: dict[str, Importance] = {"H": "H", "M": "M", "L": "L"}
_COVERAGE: dict[str, Coverage] = {"india": "india", "global": "global"}


def parse_calendar(page: str) -> list[Event]:
    """Every event the page lists, oldest first, deduplicated.

    The same release is often listed several times on one date - three
    "Corporate Bond Issuance" rows, differing only in a value column we do not
    use - so identical date-and-name pairs are collapsed.
    """
    seen: set[tuple[date, str]] = set()
    events: list[Event] = []

    for match in _ROW.finditer(page):
        event = _row_to_event(match)
        if event is None:
            continue
        key = (event.day, event.label)
        if key in seen:
            continue
        seen.add(key)
        events.append(event)

    events.sort(key=lambda e: (e.day, e.name))
    return events


def _row_to_event(match: re.Match[str]) -> Event | None:
    coverage, importance = _split_tag(match.group("tag"))
    if coverage is None or importance is None:
        return None
    day = _parse_day(match.group("date"))
    if day is None:
        return None

    cells = _CELL.findall(match.group("body"))
    if len(cells) < 2:
        return None
    raw_name = _text(cells[1])
    if not raw_name:
        return None

    name, country = _split_country(raw_name)
    return Event(
        day=day, name=name, importance=importance, coverage=coverage, country=country
    )


def _split_tag(tag: str) -> tuple[Coverage | None, Importance | None]:
    parts = tag.split("|")
    if len(parts) != 2:
        return None, None
    return _COVERAGE.get(parts[0].strip().lower()), _IMPORTANCE.get(parts[1].strip().upper())


def _parse_day(text: str) -> date | None:
    """`"Fri, 25 Sep 2026"` -> a date. The weekday prefix is decoration."""
    cleaned = text.split(",", 1)[-1].strip()
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _text(cell: str) -> str:
    stripped = html.unescape(_TAGS.sub(" ", cell))
    return _NOISE.sub("", re.sub(r"\s+", " ", stripped).strip())


def _split_country(raw: str) -> tuple[str, str | None]:
    match = _COUNTRY.match(raw)
    if match is None:
        return raw, None
    return match.group("name").strip(), match.group("country").strip()

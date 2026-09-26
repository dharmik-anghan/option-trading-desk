"""Shapes for calendar events and news headlines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

Importance = Literal["H", "M", "L"]
Coverage = Literal["india", "global"]


@dataclass(frozen=True)
class Event:
    """One scheduled economic release or policy decision.

    Date only: the calendar publishes no time of day, so an event can be known
    to fall before an expiry but not how many hours away it is.
    """

    day: date
    name: str
    importance: Importance
    coverage: Coverage
    country: str | None = None

    @property
    def label(self) -> str:
        return f"{self.name} ({self.country})" if self.country else self.name


@dataclass(frozen=True)
class Headline:
    """One news item, as a feed published it."""

    title: str
    link: str
    source: str
    published: datetime | None
    #: What its publisher writes about, copied from the source rather than read
    #: out of the headline. A beat is a fact about the publisher; a topic guessed
    #: from a title is a guess.
    topics: frozenset[str] = frozenset()

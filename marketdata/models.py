"""Bars, and what identifies a series of them.

Deliberately not tied to a venue. The desk has two brokers with their own history
endpoints and now a third source that is neither, and the point of this package is
that a candle is a candle: one store holds them all, keyed by where they came from
so that two sources disagreeing about gold does not silently merge into one series.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Interval(StrEnum):
    """Bar sizes the desk asks for.

    A closed set rather than a free string, because every source spells them
    differently - Fyers counts minutes, Shark and Yahoo use suffixes - and a
    typo'd interval otherwise becomes a separate series in the store that quietly
    never fills.
    """

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"

    @property
    def seconds(self) -> int:
        return {
            Interval.M1: 60,
            Interval.M5: 300,
            Interval.M15: 900,
            Interval.M30: 1800,
            Interval.H1: 3600,
            Interval.H4: 14400,
            Interval.D1: 86400,
        }[self]


@dataclass(frozen=True)
class Series:
    """Which bars. The store's key, and the unit a fetch covers."""

    #: Where the bars came from - "yahoo", "shark", "fyers". Part of the key
    #: because sources disagree: Yahoo's gold is a dated futures contract and
    #: Shark's is a perpetual, and merging them would invent a series that never
    #: traded.
    source: str
    #: The symbol as that source spells it. Also not translated: "GC=F" and
    #: "XAUUSDT" are different instruments, and pretending otherwise is how a chart
    #: ends up lying.
    symbol: str
    interval: Interval


@dataclass(frozen=True)
class Bar:
    """One candle. Times are UTC and mark the start of the bar."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

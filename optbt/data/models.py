"""The shapes option history is handled in."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from venues.calendar import IST

#: Every timestamp in option history is exchange-local time. A session is
#: 09:15-15:30 IST and every rule a backtest states ("enter at 09:20", "exit at
#: 15:15") is written in it, so storing UTC would put a conversion inside every
#: query for no benefit.


class Kind(StrEnum):
    CALL = "CE"
    PUT = "PE"
    FUTURE = "FUT"


@dataclass(frozen=True)
class Contract:
    """One listed F&O contract, as the expired-contracts endpoint names it."""

    symbol: str
    underlying: str
    expiry: date
    kind: Kind
    #: None for a future.
    strike: float | None


@dataclass(frozen=True)
class Candle:
    """One minute of one contract. `ts` is the start of the minute, IST, naive."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    #: Open interest in units, not lots. Zero for an index.
    oi: int


@dataclass(frozen=True)
class Expiries:
    """The expiry dates an underlying had over some span."""

    options: tuple[date, ...]
    futures: tuple[date, ...]


def from_epoch(seconds: int) -> datetime:
    """Fyers' epoch seconds to naive IST."""
    return datetime.fromtimestamp(seconds, IST).replace(tzinfo=None)

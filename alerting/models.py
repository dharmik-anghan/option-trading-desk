"""What an alert is, and the shape of the data the rules read.

The rules read data the API already assembles (a priced basket, a P&L total, a
calendar event), but this package must not import `api` - that would invert the
layering and make the engine untestable without HTTP. So the inputs are
declared here as protocols, which the API's own response models satisfy
structurally. Nothing has to be converted or copied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Protocol, runtime_checkable


class Severity(StrEnum):
    RISK = "risk"
    WARN = "warn"
    TARGET = "target"
    INFO = "info"


@dataclass(frozen=True)
class Limits:
    """The thresholds the desk alerts on."""

    #: Net profit at which to say "you're done".
    target: float = 15000.0
    #: Net loss at which to stop. Held as a positive magnitude.
    daily_loss: float = 25000.0
    #: Largest acceptable worst-case-at-expiry for one structure.
    max_loss: float = 40000.0
    #: |delta| at which a short strike counts as being tested.
    short_delta: float = 0.3
    #: Days before expiry to start warning.
    expiry_days: float = 3.0


DEFAULT_LIMITS = Limits()


class WatchKind(StrEnum):
    #: A price level on one instrument.
    PRICE = "price"
    #: A level on the book's overall P&L.
    PNL = "pnl"


class Direction(StrEnum):
    ABOVE = "above"
    BELOW = "below"


@dataclass(frozen=True)
class Watch:
    """A level you asked to be told about.

    Distinct from `Limits`, which are risk thresholds the desk applies to every
    structure. A watch is a line you drew for your own reasons - "tell me if
    NIFTY gets to 24,000" - and nothing in the data suggests it.
    """

    id: int
    kind: WatchKind
    direction: Direction
    level: float
    #: Required for a price watch, meaningless for a P&L one.
    symbol: str | None = None
    #: Your own words for it, shown instead of the raw symbol when set.
    note: str = ""
    enabled: bool = True

    @property
    def key(self) -> str:
        """Stable per level, so moving a line can fire again.

        The level is in the key on purpose. Editing 24,000 to 24,500 and having
        it stay quiet because "that alert already fired" would be wrong: it is a
        different question now.
        """
        what = self.symbol if self.kind is WatchKind.PRICE else "pnl"
        return f"watch:{self.id}:{what}:{self.direction}:{self.level:g}"


@dataclass(frozen=True)
class Condition:
    """Something that is true right now, before it becomes a logged alert."""

    #: Stable per condition, so the same condition is one alert, not one per poll.
    key: str
    severity: Severity
    message: str
    #: What it concerns - a structure's name, or None for account-wide ones.
    subject: str | None = None


@dataclass(frozen=True)
class Alert:
    """A condition that fired, with the moment it did."""

    key: str
    severity: Severity
    message: str
    #: Epoch milliseconds, matching the frontend's clock so one log can hold
    #: entries from either engine without a unit conversion.
    at: int
    subject: str | None = None


@dataclass(frozen=True)
class Outcome:
    """What one pass of the engine concluded."""

    active: frozenset[str]
    log: list[Alert] = field(default_factory=list)
    fired: list[Alert] = field(default_factory=list)


@runtime_checkable
class LegView(Protocol):
    """One leg of a structure, as far as the rules care."""

    @property
    def id(self) -> int: ...
    @property
    def side(self) -> str: ...
    @property
    def strike(self) -> float: ...
    @property
    def option_type(self) -> str: ...
    @property
    def is_open(self) -> bool: ...
    @property
    def delta(self) -> float | None: ...
    @property
    def ltp_change(self) -> float | None: ...
    @property
    def oi_change(self) -> int | None: ...


@runtime_checkable
class BasketView(Protocol):
    """A priced structure, as far as the rules care."""

    @property
    def id(self) -> int: ...
    @property
    def name(self) -> str: ...
    @property
    def expiry_date(self) -> str | None: ...
    @property
    def days_to_expiry(self) -> float | None: ...
    @property
    def max_loss(self) -> float | None: ...
    #: This structure's own levels. None means no level set - not a level of zero.
    @property
    def stop_loss(self) -> float | None: ...
    @property
    def profit_target(self) -> float | None: ...
    @property
    def delta_limit(self) -> float | None: ...
    #: What the open legs are worth now, and how the structure leans. Computed
    #: once server-side, so the screen and the alert about it read one number.
    @property
    def mtm(self) -> float | None: ...
    @property
    def net_delta(self) -> float | None: ...
    @property
    def legs(self) -> list: ...  # type: ignore[type-arg]


@runtime_checkable
class EventView(Protocol):
    """A calendar entry, as far as the rules care.

    `day` is a real date, not an ISO string. It reads as one wherever it is
    interpolated - `str(date(2026, 10, 7))` is "2026-10-07" - so alert keys and
    messages stay identical to the browser engine's, which is handed the same
    field already serialised.
    """

    @property
    def day(self) -> date: ...
    @property
    def label(self) -> str: ...
    @property
    def importance(self) -> str: ...
    @property
    def coverage(self) -> str: ...
    @property
    def country(self) -> str | None: ...

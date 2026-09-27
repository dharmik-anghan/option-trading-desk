"""What a rule says, and what a run produces."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from backtest.market import Costs, Side


class Action(StrEnum):
    """What a rule wants done at this bar."""

    NOTHING = "nothing"
    ENTER = "enter"
    EXIT = "exit"


@dataclass(frozen=True)
class Intent:
    """A rule's decision. Deliberately not an order.

    A rule says what it wants; the engine decides what that costs, whether there
    is margin for it, and at what price it could actually have happened. Keeping
    those apart is what lets the same rule drive a backtest and a live desk - a
    rule that built orders would have to know about a venue.
    """

    action: Action = Action.NOTHING
    side: Side | None = None
    #: A stop, as a price. Checked against every bar while the position is open.
    stop: float | None = None
    #: A target, likewise.
    target: float | None = None
    #: Why, for the trade log. Worth more than it looks: a list of trades with
    #: reasons is readable, and one without is a list of numbers.
    reason: str = ""

    @staticmethod
    def nothing() -> Intent:
        return Intent()

    @staticmethod
    def enter(
        side: Side, *, stop: float | None = None, target: float | None = None, reason: str = ""
    ) -> Intent:
        return Intent(action=Action.ENTER, side=side, stop=stop, target=target, reason=reason)

    @staticmethod
    def exit(reason: str = "") -> Intent:
        return Intent(action=Action.EXIT, reason=reason)


class Exit(StrEnum):
    """Why a position ended. The most useful column in a trade log.

    A rule whose trades mostly end in `STOP` is being stopped out; one that mostly
    ends in `LIQUIDATION` is sized wrong and would have been closed by the venue;
    one that ends in `EXPIRY` never decided anything and was closed by the clock.
    """

    RULE = "rule"
    STOP = "stop"
    TARGET = "target"
    LIQUIDATION = "liquidation"
    #: The run ended with the position still open.
    END_OF_DATA = "end of data"


@dataclass(frozen=True)
class Trade:
    """One round trip, closed."""

    side: Side
    quantity: float
    opened_at: datetime
    closed_at: datetime
    entry: float
    exit_price: float
    why: Exit
    reason: str
    costs: Costs
    #: Before costs: what the move was worth.
    gross: float

    @property
    def net(self) -> float:
        return self.gross - self.costs.total

    @property
    def won(self) -> bool:
        """Judged after costs, because that is the money."""
        return self.net > 0

    @property
    def bars_held(self) -> float:
        return (self.closed_at - self.opened_at).total_seconds()


@dataclass
class Position:
    """An open position, as the engine carries it."""

    side: Side
    quantity: float
    entry: float
    opened_at: datetime
    stop: float | None = None
    target: float | None = None
    liquidation: float | None = None
    reason: str = ""
    costs: Costs = field(default_factory=Costs)
    #: When funding was last charged up to, so no interval is counted twice.
    carried_to: datetime | None = None

    def gross_at(self, price: float, multiplier: float = 1.0) -> float:
        return (price - self.entry) * self.quantity * multiplier * self.side.sign

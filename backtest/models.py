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
class Trigger:
    """What has to happen after a setup before the trade is actually taken.

    A condition says a setup exists; it does not say to buy. "EMA 9 and EMA 21
    both cross below the close, then enter if the next few candles break the high
    of the candle that did it" is two different statements, and an engine that
    only understands the first has to guess the second - which it did, by always
    filling at the next open.

    So a setup arms a resting order at a level taken from the setup candle, and
    the trade happens only if price reaches it within `within` bars. If it does
    not, nothing is traded and the setup is forgotten.

    The level mirrors for a short. "Break the candle's high" is a long's way of
    saying "wait for a move in my direction", and the short's way of saying the
    same thing is the low - so a strategy written one way works both ways round
    without a second set of fields to keep in step.
    """

    #: Where the level comes from on the setup candle.
    field: str = "high"
    #: Which candle, counting back from the one the setup fired on.
    ago: int = 0
    #: How many bars the order rests for. One means the next bar only.
    within: int = 3
    #: A cushion past the level, in basis points, before the order triggers.
    #: Zero is a break of exactly the high, which in practice is a touch.
    buffer_bps: float = 0.0

    @property
    def mirrored(self) -> str:
        """The field a short uses in place of this one."""
        return {"high": "low", "low": "high"}.get(self.field, self.field)


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
    #: What has to happen before this entry becomes a trade. None means take it
    #: at the next open, which is the right default for a rule that has already
    #: decided.
    trigger: Trigger | None = None

    @staticmethod
    def nothing() -> Intent:
        return Intent()

    @staticmethod
    def enter(
        side: Side,
        *,
        stop: float | None = None,
        target: float | None = None,
        reason: str = "",
        trigger: Trigger | None = None,
    ) -> Intent:
        return Intent(
            action=Action.ENTER,
            side=side,
            stop=stop,
            target=target,
            reason=reason,
            trigger=trigger,
        )

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
    #: The condition that opened it, in the words it was built with.
    entry_reason: str
    #: What closed it. A rule's own exit says which condition fired; a stop, a
    #: target or a liquidation says so instead.
    #:
    #: Two fields rather than one, because a single `reason` was silently both:
    #: the entry's words when a stop closed the trade and the exit's words when
    #: the rule did. A log column cannot be labelled honestly against that.
    exit_reason: str
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

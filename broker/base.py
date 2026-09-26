"""The abstract broker interface, split by capability.

`strategies/`, `risk/`, and `execution/` depend only on these protocols, never
on a concrete adapter - this is the seam that makes adding a venue a matter of
writing one new adapter file rather than touching anything above it.

Why several protocols instead of one
------------------------------------
It started as a single `Broker` with every method on it, which works while
every venue is the same shape. It stops working the moment they are not: an
option chain is meaningless on a perpetual futures venue, and funding rate,
leverage and liquidation price are meaningless on an options one. A single
protocol forces every adapter to implement both and raise for half of them,
which pushes the question "can this venue actually do that?" out of the type
system and into a runtime `NotImplementedError`.

So capability is expressed as a protocol each adapter opts into, and callers
ask for the narrowest one they need. A function that reads prices takes
`MarketData` and works on any venue, today and later. A function that reads an
option chain takes `OptionsBroker` and will not typecheck against a venue that
has no chains - which is the error being caught at the right time.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Protocol, runtime_checkable

from broker.models import (
    Candle,
    Funds,
    OptionChain,
    OrderRequest,
    OrderResult,
    Position,
    Quote,
    Tick,
)
from broker.shark.models import ContractSpec, PerpPosition


@runtime_checkable
class MarketData(Protocol):
    """Prices. The one capability every venue has."""

    def get_quote(self, symbols: list[str]) -> dict[str, Quote]:
        """Fetch the latest quote for each symbol, keyed by symbol."""
        ...

    def get_history(
        self, symbol: str, resolution: str, date_from: date, date_to: date
    ) -> list[Candle]:
        """Fetch historical candles for `symbol` between two dates (inclusive)."""
        ...


@runtime_checkable
class Funds_(Protocol):
    """Account balances. Separate because a venue can trade without exposing them.

    Shark documents `GET /v1/order/futures-wallet-details` and answers 404 to it,
    so its adapter can read positions and place orders but cannot say what the
    account holds. Bundled into `Trading` that would have meant implementing a
    method that raises, which is the thing these protocols exist to avoid - and
    the pre-trade margin check would have had no way to know it was blind.
    """

    def get_funds(self) -> Funds:
        """Fetch current account balances (for pre-trade margin checks)."""
        ...


@runtime_checkable
class Trading(Protocol):
    """An account's positions, and placing orders against them."""

    def get_positions(self) -> list[Position]:
        """Fetch currently open (non-zero net quantity) positions."""
        ...

    def place_order(self, order: OrderRequest) -> OrderResult:
        """Place a real order. Irreversible - callers must confirm before calling this."""
        ...


@runtime_checkable
class OptionsData(Protocol):
    """Option chains. Only venues that list options implement this."""

    def get_option_chain(
        self, symbol: str, strike_count: int = 10, expiry_token: str = ""
    ) -> OptionChain:
        """Fetch an option chain for `symbol`.

        `expiry_token` selects which expiry; empty means the nearest one. Pass
        back a token from a previous call's `OptionChain.expiries`.
        """
        ...


@runtime_checkable
class PerpetualsData(Protocol):
    """What only a leveraged venue can answer.

    Positions again, but a different shape: `Trading.get_positions` returns the
    shared `Position`, which has no room for leverage, margin mode or the price
    at which the venue closes you out. A perps desk cannot be drawn without
    those, and an options desk has no use for them, so they are their own
    protocol rather than nullable fields on the shared one.
    """

    def get_contracts(self) -> dict[str, ContractSpec]:
        """What the venue will accept per contract: leverage ceiling, size floors.

        On the protocol rather than the adapter, so the endpoints that need these
        limits can ask for the capability instead of narrowing on a concrete class.
        They did narrow, and the effect was that anything other than the real
        adapter skipped the venue's own limits entirely - which is the opposite of
        what a check is for.
        """
        ...

    def get_perp_positions(self) -> list[PerpPosition]:
        """Open positions, with their leverage, margin and liquidation price."""
        ...

    def set_leverage(self, symbol: str, leverage: float) -> None:
        """Set the standing leverage for one contract.

        Separate from placing because the venue makes it separate: its order
        endpoint has no leverage field and applies whatever the symbol was last
        configured with. An order placed without setting this first runs at
        whatever the account happens to hold, which is not what the person who
        chose a number meant.
        """
        ...

    def set_protection(
        self,
        position_id: str,
        *,
        quantity: float,
        take_profit: float | None = None,
        stop_loss: float | None = None,
    ) -> None:
        """Have the venue hold a take-profit and stop-loss against a position.

        At the venue, not here: a stop this desk watches for stops working when a
        laptop lid shuts, and this market trades overnight.
        """
        ...


@runtime_checkable
class Streaming(Protocol):
    """Live updates pushed by the venue, consumed by a blocking caller."""

    def subscribe_ticks(self, symbols: list[str], on_tick: Callable[[Quote], None]) -> None:
        """Stream live quotes for `symbols`, calling `on_tick` for each update."""
        ...


@runtime_checkable
class AsyncStreaming(Protocol):
    """Live updates delivered on an event loop.

    A separate protocol rather than an async variant of `Streaming` because the
    two are different jobs, not two spellings of one. A blocking subscriber owns
    its thread for the life of the stream; this one is a task among others, and
    the desk's background work - the alert watcher, and the browser fan-out that
    follows - already lives on a loop.

    Kept as start/stop rather than one long-running coroutine so a caller can own
    the task's lifetime, which is what makes a clean shutdown possible.
    """

    async def start(self, symbols: list[str], on_tick: Callable[[Tick], None]) -> None:
        """Connect and subscribe. Returns once the stream is running."""
        ...

    async def stop(self) -> None:
        """Disconnect. Safe to call when not connected."""
        ...


class Broker(MarketData, Trading, Protocol):
    """The core every venue provides: prices, and an account to trade them in.

    Anything that works on any venue should be annotated with this, or with one
    of the narrower protocols if it only needs one. Note that funds are *not*
    here: see `Funds_`.
    """


class FundedBroker(Broker, Funds_, Protocol):
    """A venue that also reports what the account holds.

    What realized P&L and a margin check need. A venue that trades but does not
    expose balances satisfies `Broker` and not this, which is how the type system
    says "you cannot ask this one how much is in the account".
    """


class OptionsBroker(FundedBroker, OptionsData, Streaming, Protocol):
    """A venue that lists options - what the Indian index options desk needs.

    The streaming half is declared here rather than on `Broker` because that is
    where it is currently implemented, not because streaming is options-only.
    Move it down once a second venue streams too.
    """

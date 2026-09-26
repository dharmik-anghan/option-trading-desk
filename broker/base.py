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

from broker.models import Candle, Funds, OptionChain, OrderRequest, OrderResult, Position, Quote


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
class Trading(Protocol):
    """An account: what it holds, what it can risk, and placing orders."""

    def get_funds(self) -> Funds:
        """Fetch current account balances (for pre-trade margin checks)."""
        ...

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
class Streaming(Protocol):
    """Live updates pushed by the venue rather than polled for."""

    def subscribe_ticks(self, symbols: list[str], on_tick: Callable[[Quote], None]) -> None:
        """Stream live quotes for `symbols`, calling `on_tick` for each update."""
        ...


class Broker(MarketData, Trading, Protocol):
    """The core every venue provides: prices, and an account to trade them in.

    Anything that works on any venue should be annotated with this, or with one
    of the two halves if it only needs one.
    """


class OptionsBroker(Broker, OptionsData, Streaming, Protocol):
    """A venue that lists options - what the Indian index options desk needs.

    The streaming half is declared here rather than on `Broker` because that is
    where it is currently implemented, not because streaming is options-only.
    Move it down once a second venue streams too.
    """

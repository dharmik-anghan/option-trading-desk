"""The abstract broker interface.

`strategies/`, `risk/`, and `execution/` (once built) depend only on this
protocol, never on a concrete adapter — this is the seam that makes adding a
second broker later a matter of writing one new adapter file, not touching
anything above it.

Scope grows only as a phase actually needs it: market data (Phase 1),
account funds for margin checks (Phase 4), order placement and positions
for execution (Phase 5).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Protocol

from broker.models import Candle, Funds, OptionChain, OrderRequest, OrderResult, Position, Quote


class Broker(Protocol):
    def get_quote(self, symbols: list[str]) -> dict[str, Quote]:
        """Fetch the latest quote for each symbol, keyed by symbol."""
        ...

    def get_funds(self) -> Funds:
        """Fetch current account balances (for pre-trade margin checks)."""
        ...

    def get_option_chain(
        self, symbol: str, strike_count: int = 10, expiry_token: str = ""
    ) -> OptionChain:
        """Fetch an option chain for `symbol`.

        `expiry_token` selects which expiry; empty means the nearest one. Pass
        back a token from a previous call's `OptionChain.expiries`.
        """
        ...

    def get_history(
        self, symbol: str, resolution: str, date_from: date, date_to: date
    ) -> list[Candle]:
        """Fetch historical candles for `symbol` between two dates (inclusive)."""
        ...

    def subscribe_ticks(self, symbols: list[str], on_tick: Callable[[Quote], None]) -> None:
        """Stream live quotes for `symbols`, calling `on_tick` for each update."""
        ...

    def place_order(self, order: OrderRequest) -> OrderResult:
        """Place a real order. Irreversible - callers must confirm before calling this."""
        ...

    def get_positions(self) -> list[Position]:
        """Fetch currently open (non-zero net quantity) positions."""
        ...

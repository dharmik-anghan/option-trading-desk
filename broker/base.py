"""The abstract broker interface.

`strategies/`, `risk/`, and `execution/` (once built) depend only on this
protocol, never on a concrete adapter — this is the seam that makes adding a
second broker later a matter of writing one new adapter file, not touching
anything above it.

Scope is deliberately limited to what Phase 1 needs (market data). Order
placement, positions, and funds are added to this protocol in the phase that
actually needs them (Phase 4/5) rather than speculatively now.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Protocol

from broker.models import Candle, OptionChain, Quote


class Broker(Protocol):
    def get_quote(self, symbols: list[str]) -> dict[str, Quote]:
        """Fetch the latest quote for each symbol, keyed by symbol."""
        ...

    def get_option_chain(self, symbol: str, strike_count: int = 10) -> OptionChain:
        """Fetch the option chain for `symbol`'s nearest expiry."""
        ...

    def get_history(
        self, symbol: str, resolution: str, date_from: date, date_to: date
    ) -> list[Candle]:
        """Fetch historical candles for `symbol` between two dates (inclusive)."""
        ...

    def subscribe_ticks(self, symbols: list[str], on_tick: Callable[[Quote], None]) -> None:
        """Stream live quotes for `symbols`, calling `on_tick` for each update."""
        ...

"""A short-lived read cache in front of a broker.

Fyers rate-limits at roughly 10 requests a second and 200 a minute, and an
idle dashboard polling four endpoints breaches the per-second cap in bursts
long before it troubles the per-minute one - every poll lands in the same
instant, and `/api/portfolio` alone costs two calls (positions and funds).
The observed failure was a run of `429 request limit reached` with the desk
showing stale numbers and no explanation.

Rather than tune every caller's interval and hope, reads are cached here for
a couple of seconds. That makes the request rate a property of this file
instead of a property of how many tabs are open, and it costs nothing real:
the underlying quotes do not update faster than the TTLs below.

Writes are never cached. `place_order` goes straight through and then drops
the caches that its own result invalidates, so the next read sees the fill
rather than a stale snapshot from a moment before it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, TypeVar

from broker.base import OptionsBroker
from broker.errors import RateLimited
from broker.models import (
    Candle,
    Fill,
    Funds,
    OptionChain,
    OrderRequest,
    OrderResult,
    Position,
    Quote,
)

T = TypeVar("T")

# Seconds. Chosen against how fast each thing actually moves: prices tick
# continuously, realised P&L only changes when something fills, and the
# contract list changes once a day at most.
QUOTE_TTL = 2.0
POSITIONS_TTL = 3.0
FUNDS_TTL = 15.0
CHAIN_TTL = 5.0
HISTORY_TTL = 300.0


@dataclass
class _Entry:
    value: Any
    at: float


class CachedBroker(OptionsBroker):
    """Wraps a broker, serving repeated reads from memory for a short while."""

    def __init__(self, inner: OptionsBroker, *, now: Callable[[], float] = time.monotonic) -> None:
        self._inner = inner
        self._now = now
        self._entries: dict[tuple[str, Any], _Entry] = {}
        # Requests can overlap even on one uvicorn worker, and two arriving
        # together must not both reach the broker.
        self._lock = threading.Lock()
        self._rate_limited_at: float | None = None

    def rebind(self, inner: OptionsBroker) -> None:
        """Point at a freshly built adapter, keeping what is already cached.

        The adapter is rebuilt per request so an expired token is refreshed;
        the cached values belong to the same account regardless, so there is
        nothing to discard.
        """
        with self._lock:
            self._inner = inner

    def _cached(self, key: tuple[str, Any], ttl: float, load: Callable[[], T]) -> T:
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None and self._now() - hit.at < ttl:
                return hit.value  # type: ignore[no-any-return]

        # Fetched outside the lock: a slow call must not block unrelated reads.
        try:
            value = load()
        except RateLimited:
            # A rate limit is transient, and an expired entry is seconds old.
            # Serving it beats failing the whole panel over a blip - but the
            # fact is recorded, so the desk can say the figures are behind
            # rather than presenting them as current.
            with self._lock:
                self._rate_limited_at = self._now()
                if hit is not None:
                    return hit.value  # type: ignore[no-any-return]
            raise

        with self._lock:
            self._entries[key] = _Entry(value=value, at=self._now())
        return value

    @property
    def seconds_since_rate_limited(self) -> float | None:
        """How long ago a read was rate limited, or None if not recently."""
        with self._lock:
            if self._rate_limited_at is None:
                return None
            return self._now() - self._rate_limited_at

    def invalidate_account(self) -> None:
        """Forget positions, funds and fills: the venue says they changed."""
        self._drop("get_positions", "get_funds", "get_booked_pnl", "get_fills")

    def _drop(self, *methods: str) -> None:
        with self._lock:
            for key in [k for k in self._entries if k[0] in methods]:
                del self._entries[key]

    # ---- reads ----

    def get_quote(self, symbols: list[str]) -> dict[str, Quote]:
        key = ("get_quote", tuple(sorted(symbols)))
        return self._cached(key, QUOTE_TTL, lambda: self._inner.get_quote(symbols))

    def get_funds(self) -> Funds:
        return self._cached(("get_funds", None), FUNDS_TTL, self._inner.get_funds)

    def get_option_chain(
        self, symbol: str, strike_count: int = 10, expiry_token: str = ""
    ) -> OptionChain:
        key = ("get_option_chain", (symbol, strike_count, expiry_token))
        return self._cached(
            key,
            CHAIN_TTL,
            lambda: self._inner.get_option_chain(
                symbol, strike_count=strike_count, expiry_token=expiry_token
            ),
        )

    def get_history(
        self, symbol: str, resolution: str, date_from: date, date_to: date
    ) -> list[Candle]:
        key = ("get_history", (symbol, resolution, date_from, date_to))
        return self._cached(
            key,
            HISTORY_TTL,
            lambda: self._inner.get_history(symbol, resolution, date_from, date_to),
        )

    def get_positions(self) -> list[Position]:
        return self._cached(("get_positions", None), POSITIONS_TTL, self._inner.get_positions)

    def get_booked_pnl(self) -> float:
        load = self._inner.get_booked_pnl  # type: ignore[attr-defined]
        return float(self._cached(("get_booked_pnl", None), POSITIONS_TTL, load))

    def get_fills(self, date_from: date, date_to: date) -> list[Fill]:
        """Not cached: a reconcile that saw a stale tradebook would miss a fill."""
        return self._inner.get_fills(date_from, date_to)  # type: ignore[attr-defined, no-any-return]

    # ---- writes and streams: never cached ----

    def place_order(self, order: OrderRequest) -> OrderResult:
        result = self._inner.place_order(order)
        # What the account holds and has banked both just changed.
        self._drop("get_positions", "get_funds", "get_booked_pnl")
        return result

    def subscribe_ticks(self, symbols: list[str], on_tick: Callable[[Quote], None]) -> None:
        self._inner.subscribe_ticks(symbols, on_tick)

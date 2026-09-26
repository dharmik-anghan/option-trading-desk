from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from broker.cache import CachedBroker
from broker.fake import FakeBroker
from broker.models import (
    Candle,
    Funds,
    OptionChain,
    OrderRequest,
    OrderResult,
    Position,
    Quote,
)


class CountingBroker(FakeBroker):
    """A fake that records how many times each read actually reached it."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.calls: dict[str, int] = {}

    def _count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def get_quote(self, symbols: list[str]) -> dict[str, Quote]:
        self._count("get_quote")
        return super().get_quote(symbols)

    def get_funds(self) -> Funds:
        self._count("get_funds")
        return super().get_funds()

    def get_positions(self) -> list[Position]:
        self._count("get_positions")
        return super().get_positions()

    def get_option_chain(
        self, symbol: str, strike_count: int = 10, expiry_token: str = ""
    ) -> OptionChain:
        self._count("get_option_chain")
        return super().get_option_chain(symbol, strike_count, expiry_token)

    def get_history(
        self, symbol: str, resolution: str, date_from: date, date_to: date
    ) -> list[Candle]:
        self._count("get_history")
        return super().get_history(symbol, resolution, date_from, date_to)

    def place_order(self, order: OrderRequest) -> OrderResult:
        self._count("place_order")
        return super().place_order(order)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _pair(**kw: Any) -> tuple[CountingBroker, CachedBroker, Clock]:
    inner = CountingBroker(**kw)
    clock = Clock()
    return inner, CachedBroker(inner, now=clock), clock


def test_repeated_reads_hit_the_broker_once() -> None:
    inner, cached, _ = _pair()

    for _ in range(10):
        cached.get_positions()

    assert inner.calls["get_positions"] == 1


def test_the_cached_value_is_the_real_one() -> None:
    inner, cached, _ = _pair(
        positions=[
            Position(
                symbol="X-100-CE",
                net_quantity=-1,
                average_price=5.0,
                ltp=4.0,
                unrealized_pnl=100.0,
                product_type="MARGIN",
            )
        ]
    )

    assert cached.get_positions() == inner.get_positions()


def test_a_read_goes_through_again_once_the_ttl_passes() -> None:
    inner, cached, clock = _pair()

    cached.get_positions()
    clock.advance(2.0)
    cached.get_positions()
    assert inner.calls["get_positions"] == 1

    clock.advance(5.0)
    cached.get_positions()
    assert inner.calls["get_positions"] == 2


def test_different_arguments_are_cached_separately() -> None:
    inner, cached, _ = _pair()

    cached.get_option_chain("NSE:NIFTY50-INDEX", strike_count=5)
    cached.get_option_chain("NSE:NIFTY50-INDEX", strike_count=5)
    cached.get_option_chain("NSE:NIFTY50-INDEX", strike_count=40)
    cached.get_option_chain("BSE:SENSEX-INDEX", strike_count=5)
    cached.get_option_chain("NSE:NIFTY50-INDEX", strike_count=5, expiry_token="123")

    assert inner.calls["get_option_chain"] == 4


def test_quote_symbol_order_does_not_split_the_cache() -> None:
    inner, cached, _ = _pair()

    cached.get_quote(["A", "B"])
    cached.get_quote(["B", "A"])

    assert inner.calls["get_quote"] == 1


def test_placing_an_order_is_never_cached() -> None:
    inner, cached, _ = _pair()
    order = OrderRequest(symbol="X-100-CE", quantity=1, side="BUY")

    cached.place_order(order)
    cached.place_order(order)

    assert inner.calls["place_order"] == 2


def test_placing_an_order_invalidates_what_it_changed() -> None:
    inner, cached, _ = _pair()
    cached.get_positions()
    cached.get_funds()
    cached.get_quote(["A"])
    assert inner.calls["get_positions"] == 1

    cached.place_order(OrderRequest(symbol="X-100-CE", quantity=1, side="BUY"))

    # positions and funds must be re-read, or the desk shows a book that
    # predates the fill it just made
    cached.get_positions()
    cached.get_funds()
    assert inner.calls["get_positions"] == 2
    assert inner.calls["get_funds"] == 2
    # quotes were not affected by the order
    cached.get_quote(["A"])
    assert inner.calls["get_quote"] == 1


def test_funds_are_held_longer_than_positions() -> None:
    inner, cached, clock = _pair()
    cached.get_positions()
    cached.get_funds()

    clock.advance(5.0)  # past the positions TTL, inside the funds one
    cached.get_positions()
    cached.get_funds()

    assert inner.calls["get_positions"] == 2
    assert inner.calls["get_funds"] == 1


def test_a_burst_of_mixed_reads_costs_one_call_each() -> None:
    # the shape that tripped the rate limit: several panels polling at once
    inner, cached, _ = _pair()

    for _ in range(6):
        cached.get_positions()
        cached.get_funds()
        cached.get_quote(["NSE:NIFTY50-INDEX"])
        cached.get_option_chain("NSE:NIFTY50-INDEX", strike_count=15)

    assert sum(inner.calls.values()) == 4


class BreakingBroker(CountingBroker):
    """Fails reads on demand, to exercise the degraded paths."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.fail_with: Exception | None = None

    def get_positions(self) -> list[Position]:
        self._count("get_positions")
        if self.fail_with is not None:
            raise self.fail_with
        return FakeBroker.get_positions(self)


def test_a_rate_limit_is_ridden_out_on_the_last_good_value() -> None:
    from broker.errors import RateLimited

    inner = BreakingBroker()
    clock = Clock()
    cached = CachedBroker(inner, now=clock)

    good = cached.get_positions()
    clock.advance(10.0)  # entry has expired
    inner.fail_with = RateLimited()

    # a blip must not blank the panel; the seconds-old value is still the truth
    assert cached.get_positions() == good
    assert cached.seconds_since_rate_limited == 0.0


def test_a_rate_limit_with_nothing_cached_is_reported() -> None:
    from broker.errors import RateLimited

    inner = BreakingBroker()
    inner.fail_with = RateLimited()
    cached = CachedBroker(inner, now=Clock())

    with pytest.raises(RateLimited):
        cached.get_positions()


def test_other_failures_are_not_papered_over_with_stale_data() -> None:
    from broker.errors import BrokerUnreachable

    inner = BreakingBroker()
    clock = Clock()
    cached = CachedBroker(inner, now=clock)
    cached.get_positions()
    clock.advance(10.0)
    inner.fail_with = BrokerUnreachable()

    # being offline is not transient the way a rate limit is - say so
    with pytest.raises(BrokerUnreachable):
        cached.get_positions()


def test_nothing_reported_when_no_rate_limit_has_happened() -> None:
    _inner, cached, _ = _pair()
    cached.get_positions()
    assert cached.seconds_since_rate_limited is None

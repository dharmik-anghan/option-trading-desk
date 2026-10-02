"""The read-through cache, which exists because the source refuses.

Yahoo answers 429 after roughly ten requests in two minutes. Every test here is
ultimately about not being the reason for that: serve from disk, ask rarely, and
when refused, show what is held rather than an error.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from marketdata.models import Bar, Interval
from marketdata.service import AFTER_REFUSAL, MIN_BETWEEN_FETCHES, BarService
from marketdata.store import BarStore
from marketdata.yahoo import Fetched, RateLimited, Unavailable


class FakeSource:
    """Counts what it was asked, and answers however the test wants."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Interval, int]] = []
        self.raise_with: Exception | None = None
        self.bars: list[Bar] = []
        self.name = "Bitcoin USD"

    def fetch(self, symbol: str, interval: Interval, days: int) -> Fetched:
        self.calls.append((symbol, interval, days))
        if self.raise_with is not None:
            raise self.raise_with
        return Fetched(bars=list(self.bars), name=self.name, currency="USD")


@pytest.fixture
def store() -> Iterator[BarStore]:
    s = BarStore(":memory:")
    yield s
    s.close()


NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def bar(hours_ago: float, close: float = 100.0, at: datetime = NOW) -> Bar:
    return Bar(
        ts=at - timedelta(hours=hours_ago),
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1.0,
    )


def _service(store: BarStore, source: FakeSource, clock: dict[str, datetime]) -> BarService:
    return BarService(store, source=source, binance=source, now=lambda: clock["t"])


class TestFirstTime:
    def test_an_empty_store_fetches(self, store: BarStore) -> None:
        source = FakeSource()
        source.bars = [bar(2), bar(1)]
        svc = _service(store, source, {"t": NOW})
        out = svc.bars("BTCUSDT", Interval.H1, 5)
        assert out.fetched is True
        assert len(out.bars) == 2
        assert out.name == "Bitcoin USD"

    def test_what_was_fetched_is_kept(self, store: BarStore) -> None:
        source = FakeSource()
        source.bars = [bar(2), bar(1)]
        svc = _service(store, source, {"t": NOW})
        svc.bars("BTCUSDT", Interval.H1, 5)
        (held, count) = svc.held()[0]
        assert count == 2
        # Binance where it has the pair: a documented API with a published budget,
        # against an endpoint that refuses after about ten requests.
        assert held.source == "binance"
        assert held.symbol == "BTCUSDT"


class TestNotAskingTwice:
    def test_a_second_call_moments_later_does_not_fetch(self, store: BarStore) -> None:
        # A chart being redrawn is not a reason to ask again.
        source = FakeSource()
        source.bars = [bar(0.1)]
        clock = {"t": NOW}
        svc = _service(store, source, clock)
        svc.bars("BTCUSDT", Interval.H1, 5)
        second = svc.bars("BTCUSDT", Interval.H1, 5)
        assert len(source.calls) == 1
        assert second.fetched is False
        assert second.bars, "it still serves from disk"

    def test_it_fetches_again_once_a_bar_has_closed(self, store: BarStore) -> None:
        source = FakeSource()
        source.bars = [bar(2)]
        clock = {"t": NOW}
        svc = _service(store, source, clock)
        svc.bars("BTCUSDT", Interval.H1, 5)
        clock["t"] = NOW + MIN_BETWEEN_FETCHES + timedelta(seconds=5)
        svc.bars("BTCUSDT", Interval.H1, 5)
        assert len(source.calls) == 2

    def test_a_bar_that_has_not_closed_is_refetched_after_the_cooldown(
        self, store: BarStore
    ) -> None:
        # The opposite of what this used to assert, and the reason the options
        # chart sat still all session. A series whose newest bar had not closed was
        # called up to date and left alone - but the unclosed bar is precisely the
        # one whose high, low and close are still moving, so on a daily chart that
        # froze the right-hand edge from the first fetch of the morning onwards.
        # The cooldown is what keeps the traffic down; freshness is not.
        source = FakeSource()
        clock = {"t": NOW}
        source.bars = [bar(0, at=NOW)]
        svc = _service(store, source, clock)
        svc.bars("BTCUSDT", Interval.H1, 5)
        clock["t"] = NOW + MIN_BETWEEN_FETCHES + timedelta(seconds=5)
        out = svc.bars("BTCUSDT", Interval.H1, 5)
        assert len(source.calls) == 2
        assert out.fetched is True

    def test_a_forming_bar_is_still_not_refetched_inside_the_cooldown(
        self, store: BarStore
    ) -> None:
        source = FakeSource()
        clock = {"t": NOW}
        source.bars = [bar(0, at=NOW)]
        svc = _service(store, source, clock)
        svc.bars("BTCUSDT", Interval.H1, 5)
        clock["t"] = NOW + timedelta(seconds=10)
        out = svc.bars("BTCUSDT", Interval.H1, 5)
        assert len(source.calls) == 1
        assert out.note == "held off"

    def test_refresh_can_be_declined_outright(self, store: BarStore) -> None:
        source = FakeSource()
        svc = _service(store, source, {"t": NOW})
        out = svc.bars("BTCUSDT", Interval.H1, 5, refresh=False)
        assert source.calls == []
        assert out.fetched is False


class TestWhenTheSourceRefuses:
    def test_a_rate_limit_still_serves_what_is_held(self, store: BarStore) -> None:
        # The whole point. A chart drawn from stored bars with a note is more use
        # than an error.
        source = FakeSource()
        source.bars = [bar(3)]
        clock = {"t": NOW}
        svc = _service(store, source, clock)
        svc.bars("BTCUSDT", Interval.H1, 5)

        source.raise_with = RateLimited("Yahoo is rate limiting us")
        clock["t"] = NOW + timedelta(hours=2)
        out = svc.bars("BTCUSDT", Interval.H1, 5)
        assert out.fetched is False
        assert out.bars, "stored bars are still served"
        assert "rate limiting" in out.note

    def test_a_refusal_buys_a_longer_silence(self, store: BarStore) -> None:
        # The thing to do about a rate limit is wait, not ask more politely.
        source = FakeSource()
        source.raise_with = RateLimited("no")
        clock = {"t": NOW}
        svc = _service(store, source, clock)
        svc.bars("BTCUSDT", Interval.H1, 5)
        assert len(source.calls) == 1

        clock["t"] = NOW + MIN_BETWEEN_FETCHES + timedelta(seconds=5)
        out = svc.bars("BTCUSDT", Interval.H1, 5)
        assert len(source.calls) == 1, "asked again inside the refusal window"
        assert "held off after" in out.note

        clock["t"] = NOW + AFTER_REFUSAL + timedelta(seconds=5)
        svc.bars("BTCUSDT", Interval.H1, 5)
        assert len(source.calls) == 2

    def test_an_unavailable_source_is_reported_not_raised(self, store: BarStore) -> None:
        source = FakeSource()
        source.raise_with = Unavailable("No data found, symbol may be delisted")
        svc = _service(store, source, {"t": NOW})
        out = svc.bars("BTCUSDT", Interval.H1, 5)
        assert out.bars == []
        assert "delisted" in out.note


class TestUnmappedSymbols:
    def test_an_option_symbol_is_declined_without_a_request(self, store: BarStore) -> None:
        # The store is generic over source and symbol, but this source has no
        # instrument for an NSE option - and guessing one would draw a chart of
        # something else.
        source = FakeSource()
        svc = _service(store, source, {"t": NOW})
        out = svc.bars("NSE:NIFTY26OCT23000CE", Interval.D1, 30)
        assert out.bars == []
        assert source.calls == []
        assert "mapped" in out.note


class TestRouting:
    """Which source serves which symbol. No free source covers everything here."""

    def test_crypto_goes_to_binance(self, store: BarStore) -> None:
        svc = BarService(store, source=FakeSource(), binance=FakeSource(), now=lambda: NOW)
        assert svc.bars("BTCUSDT", Interval.D1, 30).series.source == "binance"

    def test_metals_and_oil_go_to_yahoo(self, store: BarStore) -> None:
        # Binance has no gold or oil, which is why Yahoo stays.
        svc = BarService(store, source=FakeSource(), binance=FakeSource(), now=lambda: NOW)
        assert svc.bars("XAUUSDT", Interval.D1, 30).series.source == "yahoo"
        assert svc.bars("CLUSDT", Interval.D1, 30).series.source == "yahoo"

    def test_the_two_sources_are_asked_separately(self, store: BarStore) -> None:
        yahoo, binance = FakeSource(), FakeSource()
        yahoo.bars = [bar(2)]
        binance.bars = [bar(2)]
        svc = BarService(store, source=yahoo, binance=binance, now=lambda: NOW)
        svc.bars("BTCUSDT", Interval.D1, 30)
        svc.bars("XAUUSDT", Interval.D1, 30)
        assert [c[0] for c in binance.calls] == ["BTCUSDT"]
        assert [c[0] for c in yahoo.calls] == ["GC=F"]

    def test_an_unmapped_symbol_reaches_neither(self, store: BarStore) -> None:
        yahoo, binance = FakeSource(), FakeSource()
        svc = BarService(store, source=yahoo, binance=binance, now=lambda: NOW)
        out = svc.bars("NSE:NIFTY50-INDEX", Interval.D1, 30)
        assert yahoo.calls == [] and binance.calls == []
        assert "No source" in out.note

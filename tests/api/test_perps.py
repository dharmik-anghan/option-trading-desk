"""The perpetuals desk endpoints.

No lifespan runs in these tests, so there is no tick hub and no stream - which is
worth testing in its own right: the desk has to describe itself before a price has
ever arrived, and it must not report a zero as though it were a market.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient

from api.routers import perps as perps_router
from broker.errors import BrokerError
from broker.models import Candle, OrderResult, Tick
from broker.models import OrderRequest as BrokerOrderRequest
from broker.shark.models import ContractSpec, PerpPosition
from streaming import TickHub


class TestTheDesk:
    def test_it_describes_itself(self, client: TestClient) -> None:
        body = client.get("/api/perps").json()
        assert body["venue"] == "shark"
        assert [i["symbol"] for i in body["instruments"]] == [
            "BTCUSDT",
            "ETHUSDT",
            "XAUUSDT",
            "CLUSDT",
        ]

    def test_prices_and_money_are_different_currencies(self, client: TestClient) -> None:
        # The asymmetry that makes this venue awkward: charts in USDT, account in
        # INR. Reporting one currency would mislabel one of the two.
        body = client.get("/api/perps").json()
        assert body["quote_currency"] == "USDT"
        assert body["money_currency"] == "INR"

    def test_every_instrument_is_open_whatever_the_day(self, client: TestClient) -> None:
        # These perpetuals run continuously, gold and oil included. Checked
        # against a live weekend stream, which delivered all three seconds old -
        # the underlying futures close, the contract does not.
        body = client.get("/api/perps").json()
        assert all(i["open"] is True for i in body["instruments"])

    def test_prices_are_null_before_any_tick(self, client: TestClient) -> None:
        # Null, not zero. Zero is a market at nothing, which would move every
        # level being watched.
        body = client.get("/api/perps").json()
        assert all(p["price"] is None for p in body["prices"])
        assert all(p["age_seconds"] is None for p in body["prices"])

    def test_it_reports_the_stream_as_down_when_there_is_none(self, client: TestClient) -> None:
        # An empty price list means "no data", and the desk should be able to say
        # which of the two it is.
        assert client.get("/api/perps").json()["stream"]["connected"] is False

    def test_a_published_tick_shows_up(self, client: TestClient) -> None:
        from api.app import app

        hub = TickHub()
        app.state.tick_hub = hub
        try:
            hub.publish(Tick(symbol="BTCUSDT", price=84105.1, at=datetime.now(UTC)))
            prices = {p["symbol"]: p for p in client.get("/api/perps").json()["prices"]}
            assert prices["BTCUSDT"]["price"] == 84105.1
            assert prices["BTCUSDT"]["age_seconds"] is not None
            assert prices["XAUUSDT"]["price"] is None
        finally:
            app.state.tick_hub = None


SPECS = {
    "BTCUSDT": ContractSpec(
        symbol="BTCUSDT",
        max_leverage=150.0,
        min_quantity=0.001,
        min_notional=115.0,
        price_dp=1,
        quantity_dp=3,
        maintenance_margin_pct=15.0,
    ),
    "ETHUSDT": ContractSpec(
        symbol="ETHUSDT",
        max_leverage=150.0,
        min_quantity=0.001,
        min_notional=23.0,
        price_dp=2,
        quantity_dp=3,
        maintenance_margin_pct=15.0,
    ),
    "XAUUSDT": ContractSpec(
        symbol="XAUUSDT",
        max_leverage=75.0,
        min_quantity=0.001,
        min_notional=5.75,
        price_dp=2,
        quantity_dp=3,
        maintenance_margin_pct=15.0,
    ),
    "CLUSDT": ContractSpec(
        symbol="CLUSDT",
        max_leverage=50.0,
        min_quantity=0.01,
        min_notional=5.75,
        price_dp=2,
        quantity_dp=2,
        maintenance_margin_pct=35.0,
    ),
}


class Stub:
    """A venue with one open position, since the real account has none.

    The open-position shape is the one thing in the Shark integration not taken
    from a live example, so the path that renders it - a derived P&L, a liquidation
    distance - would otherwise be untested until the first real position, which is
    the worst moment to find out.
    """

    def __init__(self, position: PerpPosition | None = None, fail: bool = False) -> None:
        self._position = position
        self._fail = fail
        self.protection: list[tuple[str, float, float | None, float | None]] = []
        #: Orders this stub was asked to send. Nothing leaves the process.
        self.orders: list[BrokerOrderRequest] = []
        self.refuse_order: str | None = None
        #: (symbol, leverage) pairs this stub was asked to configure.
        self.leverage_set: list[tuple[str, float]] = []
        #: (symbol, leverage, margin mode) for each preference call.
        self.preference_set: list[tuple[str, float, str]] = []
        self.refuse_leverage: str | None = None
        #: (symbol, side, quantity) for each close asked of this stub.
        self.closed: list[tuple[str, str, float]] = []
        self.refuse_close: str | None = None
        self.history: list[Candle] = []

    def get_contracts(self) -> dict[str, ContractSpec]:
        """The real venue's published limits, copied once into SPECS above."""
        return SPECS

    def get_history(self, symbol: str, resolution: str, date_from: object, date_to: object) -> list:  # type: ignore[type-arg]
        """Whatever a test put in `history`, and nothing by default.

        Empty is right for most of these: the parsers are tested against real
        responses in tests/broker/. A test that needs a series - anything about
        drawing on the chart - fills this in.
        """
        return self.history

    def place_order(self, order: BrokerOrderRequest) -> OrderResult:
        if self.refuse_order is not None:
            raise BrokerError(self.refuse_order)
        self.orders.append(order)
        return OrderResult(order_id="stub-order-1", message="OPEN")

    def get_perp_positions(self) -> list[PerpPosition]:
        if self._fail:
            raise BrokerError("Shark: signature rejected")
        return [self._position] if self._position is not None else []

    def close_position(self, position: PerpPosition) -> OrderResult:
        if self.refuse_close is not None:
            raise BrokerError(self.refuse_close)
        self.closed.append((position.symbol, position.side, position.quantity))
        return OrderResult(order_id="stub-close-1", message="closed")

    def set_preference(self, symbol: str, leverage: float, margin_mode: str) -> None:
        """Recorded, and required by the protocol - so a stub that omits it stops
        satisfying PerpetualsData, which is how a new step in the order path gets
        noticed here rather than in production."""
        if self.refuse_leverage is not None:
            raise BrokerError(self.refuse_leverage)
        self.preference_set.append((symbol, leverage, margin_mode))

    def set_leverage(self, symbol: str, leverage: float) -> None:
        if self.refuse_leverage is not None:
            raise BrokerError(self.refuse_leverage)
        self.leverage_set.append((symbol, leverage))

    def set_protection(
        self,
        position_id: str,
        *,
        quantity: float,
        take_profit: float | None = None,
        stop_loss: float | None = None,
    ) -> None:
        """Recorded, never sent. The protocol requires it, so a stub that omitted
        it would stop satisfying `PerpetualsData` - which is how this test caught
        the method being added."""
        if self._fail:
            raise BrokerError("Shark: signature rejected")
        self.protection.append((position_id, quantity, take_profit, stop_loss))


@pytest.fixture(autouse=True)
def stub_venue(monkeypatch: pytest.MonkeyPatch) -> Stub:
    """Every test here gets a venue that records instead of trading.

    Autouse because forgetting is the failure mode that matters: without it a
    sound order reaches the real account, which is exactly what happened once.
    """
    stub = Stub()
    monkeypatch.setattr(perps_router, "broker_for", lambda spec: stub)
    return stub


def _position(**over: object) -> PerpPosition:
    fields: dict[str, object] = {
        "symbol": "XAUUSDT",
        "side": "SHORT",
        "quantity": 0.01,
        "entry_price": 4300.0,
        "mark_price": None,
        "leverage": 8.0,
        "liquidation_price": 4730.0,
        "margin_type": "ISOLATED",
        "margin": 5.0,
        "margin_in_margin_asset": 510.0,
        "margin_asset": "INR",
        "unrealized_pnl": None,
        "unrealized_pnl_in_margin_asset": None,
        "position_id": "p-1",
        "take_profit_orders": 0,
        "stop_loss_orders": 0,
    }
    fields.update(over)
    return PerpPosition(**fields)  # type: ignore[arg-type]


class TestPositions:
    def test_no_positions_reads_as_none_open(self, client: TestClient) -> None:
        body = client.get("/api/perps").json()
        assert body["positions"] == []
        assert body["positions_error"] is None

    def test_a_position_is_reported_with_what_decides_its_survival(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(perps_router, "broker_for", lambda spec: Stub(_position()))
        (p,) = client.get("/api/perps").json()["positions"]
        assert p["side"] == "SHORT"
        assert p["name"] == "Gold"  # named, not left as the ticker
        assert p["leverage"] == 8
        assert p["margin_type"] == "ISOLATED"
        assert p["liquidation_price"] == 4730.0

    def test_pnl_is_derived_from_the_stream_when_the_venue_gives_none(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from api.app import app

        hub = TickHub()
        hub.publish(Tick(symbol="XAUUSDT", price=4200.0, at=datetime.now(UTC)))
        app.state.tick_hub = hub
        monkeypatch.setattr(perps_router, "broker_for", lambda spec: Stub(_position()))
        try:
            (p,) = client.get("/api/perps").json()["positions"]
            # Short 0.01 from 4300, now 4200: a gain of one rupee-equivalent.
            assert p["price"] == 4200.0
            assert p["unrealized_pnl"] == pytest.approx((4300.0 - 4200.0) * 0.01)
            assert p["pnl_is_ours"] is True, "a figure we derived must say so"
            # 4730 against 4200 is 12.6% away
            assert p["liquidation_distance"] == pytest.approx((4730.0 - 4200.0) / 4200.0)
        finally:
            app.state.tick_hub = None

    def test_the_venues_own_pnl_is_preferred(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            perps_router, "broker_for", lambda spec: Stub(_position(unrealized_pnl=-0.42))
        )
        (p,) = client.get("/api/perps").json()["positions"]
        assert p["unrealized_pnl"] == -0.42
        assert p["pnl_is_ours"] is False

    def test_a_failure_is_distinguished_from_an_empty_book(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An empty list because a request failed looks exactly like an empty list
        # because nothing is open, and on a leveraged book those are very
        # different things to be told.
        monkeypatch.setattr(perps_router, "broker_for", lambda spec: Stub(fail=True))
        body = client.get("/api/perps").json()
        assert body["positions"] == []
        assert body["positions_error"] is not None
        assert "signature" in body["positions_error"]


class TestProtection:
    """Asking the venue to hold a stop. Never sent from a test - recorded."""

    def test_a_stop_reaches_the_venue_with_the_position_size(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = Stub(_position())
        monkeypatch.setattr(perps_router, "broker_for", lambda spec: stub)
        response = client.post(
            "/api/perps/positions/p-1/protection",
            json={"quantity": 0.01, "stop_loss": 4500},
        )
        assert response.status_code == 204
        assert stub.protection == [("p-1", 0.01, None, 4500.0)]

    def test_both_levels_can_be_set_at_once(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = Stub(_position())
        monkeypatch.setattr(perps_router, "broker_for", lambda spec: stub)
        client.post(
            "/api/perps/positions/p-1/protection",
            json={"quantity": 0.01, "stop_loss": 4500, "take_profit": 4100},
        )
        assert stub.protection == [("p-1", 0.01, 4100.0, 4500.0)]

    def test_asking_for_neither_is_refused(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = Stub(_position())
        monkeypatch.setattr(perps_router, "broker_for", lambda spec: stub)
        assert (
            client.post("/api/perps/positions/p-1/protection", json={"quantity": 0.01}).status_code
            == 422
        )
        assert stub.protection == []

    def test_a_zero_quantity_is_refused(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = Stub(_position())
        monkeypatch.setattr(perps_router, "broker_for", lambda spec: stub)
        assert (
            client.post(
                "/api/perps/positions/p-1/protection",
                json={"quantity": 0, "stop_loss": 1},
            ).status_code
            == 422
        )
        assert stub.protection == []

    def test_a_venue_refusal_is_reported_not_swallowed(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Believing a stop is attached when it is not is worse than knowing there
        # is none.
        monkeypatch.setattr(perps_router, "broker_for", lambda spec: Stub(fail=True))
        response = client.post(
            "/api/perps/positions/p-1/protection",
            json={"quantity": 0.01, "stop_loss": 1},
        )
        assert response.status_code == 502
        assert "signature" in response.json()["detail"]

    def test_an_unprotected_position_says_so(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(perps_router, "broker_for", lambda spec: Stub(_position()))
        (p,) = client.get("/api/perps").json()["positions"]
        assert p["protected"] is False

    def test_a_protected_position_says_so(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        held = _position(stop_loss_orders=1)
        monkeypatch.setattr(perps_router, "broker_for", lambda spec: Stub(held))
        (p,) = client.get("/api/perps").json()["positions"]
        assert p["protected"] is True
        assert p["stop_loss_orders"] == 1


class TestPlacingAnOrder:
    """The order path. Nothing leaves the process, because `stub_venue` replaces
    the adapter - not because of any setting, since there is no longer one.

    What is asserted is the order of operations: checks server-side so a client
    cannot skip them, a refused order never reaching the venue, and the attempt
    logged either way.
    """

    def _order(self, **over: object) -> dict[str, object]:
        body: dict[str, object] = {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": 0.002,
            "leverage": 8,
        }
        body.update(over)
        return body

    def _priced(self, price: float = 84_000.0) -> TickHub:
        hub = TickHub()
        hub.publish(Tick(symbol="BTCUSDT", price=price, at=datetime.now(UTC)))
        return hub

    def test_an_unlisted_instrument_is_refused(self, client: TestClient) -> None:
        response = client.post("/api/perps/orders", json=self._order(symbol="DOGEUSDT"))
        assert response.status_code == 404

    def test_no_price_means_no_order(self, client: TestClient) -> None:
        # The notional cap cannot be applied without one, and an uncapped market
        # order is the thing the caps exist to prevent.
        assert client.post("/api/perps/orders", json=self._order()).status_code == 503

    def test_leverage_is_set_before_the_order(
        self, client: TestClient, stub_venue: Stub
    ) -> None:
        # The venue's order endpoint has no leverage field and applies whatever the
        # symbol was last configured with. Ours sent none, so an order placed at a
        # chosen 10x actually ran at the account's standing 150x - the maximum -
        # with liquidation 0.42% from entry.
        from api.app import app

        app.state.tick_hub = self._priced()
        try:
            client.post("/api/perps/orders", json=self._order(leverage=10))
        finally:
            app.state.tick_hub = None
        assert stub_venue.preference_set == [("BTCUSDT", 10.0, "ISOLATED")]
        assert len(stub_venue.orders) == 1

    def test_margin_mode_defaults_to_isolated_rather_than_inherited(
        self, client: TestClient, stub_venue: Stub
    ) -> None:
        # Same trap leverage had: an order carries neither, so both come from
        # whatever the symbol was last set to. ISOLATED risks only the margin
        # behind the position; CROSS puts the rest of the account behind it.
        from api.app import app

        app.state.tick_hub = self._priced()
        try:
            client.post("/api/perps/orders", json=self._order())
        finally:
            app.state.tick_hub = None
        assert stub_venue.preference_set[0][2] == "ISOLATED"

    def test_cross_can_be_asked_for_explicitly(
        self, client: TestClient, stub_venue: Stub
    ) -> None:
        from api.app import app

        app.state.tick_hub = self._priced()
        try:
            client.post("/api/perps/orders", json=self._order(margin_mode="CROSS"))
        finally:
            app.state.tick_hub = None
        assert stub_venue.preference_set[0][2] == "CROSS"

    def test_a_nonsense_margin_mode_is_refused(self, client: TestClient) -> None:
        response = client.post("/api/perps/orders", json=self._order(margin_mode="WHATEVER"))
        assert response.status_code == 422

    def test_a_failed_leverage_change_abandons_the_order(
        self, client: TestClient, stub_venue: Stub
    ) -> None:
        # An order at 150x when 10x was asked for is worse than no order.
        from api.app import app

        stub_venue.refuse_leverage = "Leverage not allowed"
        app.state.tick_hub = self._priced()
        try:
            body = client.post("/api/perps/orders", json=self._order(leverage=10)).json()
        finally:
            app.state.tick_hub = None
        assert body["sent"] is False
        assert stub_venue.orders == [], "nothing may be placed at unknown leverage"
        assert "Leverage not allowed" in body["outcome"]

    def test_a_sound_order_passes_every_check_and_is_sent(
        self, client: TestClient, stub_venue: Stub
    ) -> None:
        from api.app import app

        app.state.tick_hub = self._priced()
        try:
            body = client.post("/api/perps/orders", json=self._order()).json()
            assert body["reasons"] == []
            assert all(c["passed"] for c in body["checks"]), body["checks"]
            assert body["sent"] is True
            assert body["notional"] == pytest.approx(0.002 * 84_000)
            assert body["venue_order_id"] == "stub-order-1"
            # and it reached the venue exactly once
            assert len(stub_venue.orders) == 1
            assert stub_venue.orders[0].quantity == 0.002
        finally:
            app.state.tick_hub = None

    def test_a_refused_order_never_reaches_the_venue(
        self, client: TestClient, stub_venue: Stub
    ) -> None:
        # The caps are what stands between a mistake in this program and a
        # position, so a failed check must stop the request rather than colour the
        # result afterwards.
        from api.app import app

        app.state.tick_hub = self._priced()
        try:
            client.post("/api/perps/orders", json=self._order(quantity=5))
        finally:
            app.state.tick_hub = None
        assert stub_venue.orders == []

    def test_a_venue_refusal_is_recorded_as_unsent(
        self, client: TestClient, stub_venue: Stub
    ) -> None:
        from api.app import app

        stub_venue.refuse_order = "Insufficient margin"
        app.state.tick_hub = self._priced()
        try:
            body = client.post("/api/perps/orders", json=self._order()).json()
        finally:
            app.state.tick_hub = None
        assert body["sent"] is False
        (row,) = client.get("/api/perps/orders").json()
        assert row["sent"] is False
        assert "Insufficient margin" in row["reason"]

    def test_a_size_under_the_venues_minimum_is_refused(self, client: TestClient) -> None:
        # BTCUSDT allows 0.001 but demands 115 USDT, so at 84,000 the floor is
        # 0.002 - and the desk should say so rather than let the venue say "failed".
        from api.app import app

        app.state.tick_hub = self._priced()
        try:
            body = client.post("/api/perps/orders", json=self._order(quantity=0.001)).json()
            assert body["sent"] is False
            assert any("minimum" in r for r in body["reasons"])
        finally:
            app.state.tick_hub = None

    def test_a_fat_finger_is_caught_by_the_notional_cap(self, client: TestClient) -> None:
        # 0.002 typed as 2 is 168,000 of notional. Caught in money rather than in
        # contracts, because a quantity cap cannot be compared across instruments
        # worth 840 USDT and 94 cents apiece - one tight enough for Bitcoin blocks
        # the smallest legal order in oil.
        from api.app import app

        app.state.tick_hub = self._priced()
        try:
            body = client.post("/api/perps/orders", json=self._order(quantity=2)).json()
            assert body["sent"] is False
            assert any("Notional" in r for r in body["reasons"]), body["reasons"]
        finally:
            app.state.tick_hub = None

    def test_leverage_past_the_venues_own_maximum_is_refused(self, client: TestClient) -> None:
        # BTCUSDT allows 150x, so 151 is the venue's own line rather than ours.
        from api.app import app

        app.state.tick_hub = self._priced()
        try:
            body = client.post("/api/perps/orders", json=self._order(leverage=151)).json()
            assert any("Leverage" in r for r in body["reasons"])
        finally:
            app.state.tick_hub = None

    def test_leverage_the_venue_allows_is_not_refused(self, client: TestClient) -> None:
        # 100x on BTCUSDT is within the venue's 150x. Whether it is wise is not
        # this program's opinion to hold - it caps what it might do by mistake.
        from api.app import app

        app.state.tick_hub = self._priced()
        try:
            body = client.post("/api/perps/orders", json=self._order(leverage=100)).json()
            assert not any("Leverage" in r for r in body["reasons"])
        finally:
            app.state.tick_hub = None

    def test_a_limit_order_without_a_price_is_refused_by_validation(
        self, client: TestClient
    ) -> None:
        assert (
            client.post("/api/perps/orders", json=self._order(order_type="LIMIT")).status_code
            == 422
        )

    def test_a_limit_order_is_sized_against_its_own_price(self, client: TestClient) -> None:
        # Not against the stream: the order is at the price given, and capping it
        # against a different number would cap the wrong order.
        from api.app import app

        app.state.tick_hub = self._priced(84_000.0)
        try:
            body = client.post(
                "/api/perps/orders",
                json=self._order(order_type="LIMIT", limit_price=80_000.0),
            ).json()
            assert body["price"] == 80_000.0
            assert body["notional"] == pytest.approx(0.002 * 80_000)
        finally:
            app.state.tick_hub = None

    def test_every_attempt_is_logged_including_refusals(self, client: TestClient) -> None:
        from api.app import app

        app.state.tick_hub = self._priced()
        try:
            client.post("/api/perps/orders", json=self._order())
            client.post("/api/perps/orders", json=self._order(quantity=2))
        finally:
            app.state.tick_hub = None
        log = client.get("/api/perps/orders").json()
        assert len(log) == 2
        # newest first, and the refusal carries its reason
        assert log[0]["quantity"] == 2
        assert "Notional" in log[0]["reason"]
        assert log[0]["sent"] is False
        assert log[1]["sent"] is True, "the sound one was sent"


class TestTheStream:
    """Pushing prices instead of being asked for them.

    The socket to the exchange was always there; this is the half that was missing,
    and why /api/perps was being polled every two seconds.

    Driven through the endpoint's own generator rather than TestClient. An SSE
    response never ends, and TestClient waits for the body to finish - so a test
    that reads one frame and breaks hangs until it is killed, which is how the
    first attempt at this went.
    """

    async def _events(self, hub: TickHub | None) -> AsyncGenerator[str, None]:
        """The endpoint's own frame generator, with a request stubbed to the hub."""
        from starlette.requests import Request as StarletteRequest

        class FakeRequest:
            """Enough of a request for the generator: app state, and connected."""

            def __init__(self) -> None:
                self.app = SimpleNamespace(state=SimpleNamespace(tick_hub=hub))

            async def is_disconnected(self) -> bool:
                return False

        response = await perps_router.stream(cast(StarletteRequest, FakeRequest()))
        return cast(AsyncGenerator[str, None], response.body_iterator)

    def test_it_sends_what_is_already_known_first(self) -> None:
        # A page that has just loaded should not be blank until something moves.
        hub = TickHub()
        hub.publish(Tick(symbol="BTCUSDT", price=84_000.0, at=datetime.now(UTC)))

        async def read_one() -> str:
            events = await self._events(hub)
            return await asyncio.wait_for(anext(events), timeout=2)

        frame = asyncio.run(read_one())
        assert "BTCUSDT" in frame
        assert "84000" in frame

    def test_a_later_tick_is_pushed(self) -> None:
        hub = TickHub()

        async def read_pushed() -> str:
            events = await self._events(hub)
            task: asyncio.Task[str] = asyncio.create_task(anext(events))
            await asyncio.sleep(0)  # let it subscribe
            hub.publish(Tick(symbol="XAUUSDT", price=4285.6, at=datetime.now(UTC)))
            return await asyncio.wait_for(task, timeout=2)

        frame = asyncio.run(read_pushed())
        assert "XAUUSDT" in frame
        assert "4285.6" in frame

    def test_no_hub_says_so_rather_than_hanging(self) -> None:
        # Holding a connection open that will never carry anything is worse than
        # saying the stream never started.
        async def read_one() -> str:
            events = await self._events(None)
            return await asyncio.wait_for(anext(events), timeout=2)

        frame = asyncio.run(read_one())
        assert "closed" in frame
        assert "no price stream" in frame

    def test_a_reader_is_unregistered_when_it_leaves(self) -> None:
        # A browser closing a tab must not leave a queue growing behind it.
        hub = TickHub()
        hub.publish(Tick(symbol="BTCUSDT", price=1.0, at=datetime.now(UTC)))

        async def read_then_close() -> None:
            events = await self._events(hub)
            await asyncio.wait_for(anext(events), timeout=2)
            assert hub.subscriber_count == 1
            await events.aclose()

        asyncio.run(read_then_close())
        assert hub.subscriber_count == 0


class TestClosingAPosition:
    """Closing, which the desk could not do - you had to go to the venue's website.

    The asymmetry mattered: a desk that can open and not close is a desk you watch
    a problem from rather than act on.
    """

    def test_it_closes_with_the_venues_own_view_of_the_size(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Read now, not sent by the client: a stale quantity from a page that has
        # not refreshed would leave a remainder open.
        stub = Stub(_position(quantity=0.002, side="LONG"))
        monkeypatch.setattr(perps_router, "broker_for", lambda spec: stub)
        body = client.post("/api/perps/positions/p-1/close").json()
        assert body["closed"] is True
        assert stub.closed == [("XAUUSDT", "LONG", 0.002)]

    def test_a_position_that_has_gone_is_a_404_not_a_success(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A stop may have fired, or it was closed elsewhere. Not alarming, but not
        # something to report as done either.
        monkeypatch.setattr(perps_router, "broker_for", lambda spec: Stub(_position()))
        assert client.post("/api/perps/positions/not-open/close").status_code == 404

    def test_a_venue_refusal_is_reported_and_recorded(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = Stub(_position())
        stub.refuse_close = "Insufficient margin"
        monkeypatch.setattr(perps_router, "broker_for", lambda spec: stub)
        response = client.post("/api/perps/positions/p-1/close")
        assert response.status_code == 502
        (row,) = client.get("/api/perps/orders").json()
        assert row["sent"] is False
        assert "Insufficient margin" in row["reason"]

    def test_a_close_is_in_the_order_log(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = Stub(_position(quantity=0.002, side="SHORT"))
        monkeypatch.setattr(perps_router, "broker_for", lambda spec: stub)
        client.post("/api/perps/positions/p-1/close")
        (row,) = client.get("/api/perps/orders").json()
        assert row["sent"] is True
        # a short closes by buying
        assert row["side"] == "BUY"
        assert row["quantity"] == 0.002

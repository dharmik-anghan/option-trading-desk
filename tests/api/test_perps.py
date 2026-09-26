"""The perpetuals desk endpoints.

No lifespan runs in these tests, so there is no tick hub and no stream - which is
worth testing in its own right: the desk has to describe itself before a price has
ever arrived, and it must not report a zero as though it were a market.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from api.routers import perps as perps_router
from broker.errors import BrokerError
from broker.models import Tick
from broker.shark.models import PerpPosition
from streaming import TickHub


class TestTheDesk:
    def test_it_describes_itself(self, client: TestClient) -> None:
        body = client.get("/api/perps").json()
        assert body["venue"] == "shark"
        assert [i["symbol"] for i in body["instruments"]] == ["BTCUSDT", "XAUUSDT", "CLUSDT"]

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


class TestCandles:
    def test_an_unlisted_symbol_is_a_404(self, client: TestClient) -> None:
        assert client.get("/api/perps/candles/DOGEUSDT").status_code == 404

    def test_a_listed_symbol_reaches_the_broker(self, client: TestClient) -> None:
        # The fake broker in these tests is the options one, so this asks only
        # that routing and validation work - the shapes are covered by
        # tests/broker/test_shark_parse.py against real responses.
        response = client.get("/api/perps/candles/BTCUSDT")
        assert response.status_code in {200, 502}


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

    def get_perp_positions(self) -> list[PerpPosition]:
        if self._fail:
            raise BrokerError("Shark: signature rejected")
        return [self._position] if self._position is not None else []


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
        "margin_asset": "INR",
        "unrealized_pnl": None,
        "unrealized_pnl_in_margin_asset": None,
        "position_id": "p-1",
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

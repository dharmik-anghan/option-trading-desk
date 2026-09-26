"""The perpetuals desk endpoints.

No lifespan runs in these tests, so there is no tick hub and no stream - which is
worth testing in its own right: the desk has to describe itself before a price has
ever arrived, and it must not report a zero as though it were a market.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from broker.models import Tick
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

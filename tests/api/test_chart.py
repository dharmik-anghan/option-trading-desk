"""The one chart endpoint, which both desks draw from.

There used to be two: the perpetuals desk had its own, and the options desk got
its candles folded into the structure reading. These tests moved here with the
endpoint, and they still describe the same behaviour - which is the point of the
move, since that behaviour now belongs to both desks rather than one.

No bar store runs in these tests, so they exercise the path where the store is
unavailable and the venue is asked directly. That is a real path: a backfill
script holds the store for a minute at a time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api.routers import chart as chart_router
from broker.models import Candle


class StubHistory:
    """A venue that serves whatever a test put in `history`."""

    def __init__(self) -> None:
        self.history: list[Candle] = []
        self.asked: list[tuple[str, str]] = []

    def get_history(
        self, symbol: str, resolution: str, date_from: object, date_to: object
    ) -> list[Candle]:
        self.asked.append((symbol, resolution))
        return self.history


@pytest.fixture(autouse=True)
def venue(monkeypatch: pytest.MonkeyPatch) -> StubHistory:
    stub = StubHistory()
    monkeypatch.setattr(chart_router, "broker_for", lambda spec: stub)
    return stub


def _candles(n: int = 120) -> list[Candle]:
    """A rising hourly series for the chart tests to draw on."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(
            timestamp=start + timedelta(hours=i),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1.0,
        )
        for i in range(n)
    ]


class TestWhatItWillDraw:
    def test_a_symbol_the_venue_does_not_list_is_a_404(self, client: TestClient) -> None:
        """A chart drawn for a symbol nobody listed is a stranger's price with an
        order ticket beside it."""
        assert client.get("/api/chart/shark/DOGEUSDT").status_code == 404

    def test_a_listed_symbol_is_drawn(self, client: TestClient, venue: StubHistory) -> None:
        venue.history = _candles()
        response = client.get("/api/chart/shark/BTCUSDT", params={"interval": "1h"})
        assert response.status_code == 200
        assert len(response.json()["candles"]) == 120

    def test_a_size_that_is_not_a_bar_size_is_refused(self, client: TestClient) -> None:
        assert client.get("/api/chart/shark/BTCUSDT", params={"interval": "3h"}).status_code == 400

    def test_the_venue_is_asked_in_the_size_the_caller_named(
        self, client: TestClient, venue: StubHistory
    ) -> None:
        venue.history = _candles()
        client.get("/api/chart/shark/BTCUSDT", params={"interval": "4h"})
        assert venue.asked == [("BTCUSDT", "4h")]


class TestIndicators:
    def test_they_are_drawn_through_the_same_code_a_rule_reads_them_with(
        self, client: TestClient, venue: StubHistory
    ) -> None:
        """The EMA on this chart and the EMA a rule trades on are one number."""
        venue.history = _candles()

        response = client.get(
            "/api/chart/shark/BTCUSDT",
            params={"interval": "1h", "days": 5, "indicators": "ema:5,rsi:14"},
        )

        assert response.status_code == 200
        body = response.json()
        labels = {line["label"]: line for line in body["lines"]}
        assert set(labels) == {"EMA 5", "RSI 14"}
        for line in body["lines"]:
            assert len(line["values"]) == len(body["candles"])
        # an RSI is 0-100 and would be a flat line along the bottom of a price chart
        assert labels["EMA 5"]["on_price"] is True
        assert labels["RSI 14"]["on_price"] is False

    def test_none_asked_for_means_none_drawn(self, client: TestClient) -> None:
        body = client.get("/api/chart/shark/BTCUSDT", params={"interval": "1h"}).json()
        assert body["lines"] == []

    def test_an_unreadable_one_is_dropped_rather_than_refusing_the_chart(
        self, client: TestClient, venue: StubHistory
    ) -> None:
        """A chart is worth drawing without a line somebody mistyped. The endpoint
        that has to be strict about this is the one that runs a strategy."""
        venue.history = _candles()

        response = client.get(
            "/api/chart/shark/BTCUSDT",
            params={"interval": "1h", "indicators": "ema:20,macd:9,ema:notanumber"},
        )

        assert response.status_code == 200
        assert [line["label"] for line in response.json()["lines"]] == ["EMA 20"]

    def test_one_cannot_be_read_on_a_shorter_timeframe_than_the_chart(
        self, client: TestClient, venue: StubHistory
    ) -> None:
        """The same rule the backtester enforces, for the same reason: a
        five-minute line on an hourly chart shows bars the chart does not have."""
        venue.history = _candles()

        response = client.get(
            "/api/chart/shark/BTCUSDT",
            params={"interval": "1h", "indicators": "ema:20:5m,ema:50:4h"},
        )

        assert [line["label"] for line in response.json()["lines"]] == ["EMA 50 4h"]


class TestTheWindow:
    def test_bars_trims_to_the_last_n(self, client: TestClient, venue: StubHistory) -> None:
        venue.history = _candles()
        body = client.get(
            "/api/chart/shark/BTCUSDT", params={"interval": "1h", "bars": 30}
        ).json()
        assert len(body["candles"]) == 30
        assert body["candles"][-1]["close"] == 100.5 + 119

    def test_an_indicator_is_warmed_up_before_the_window_it_is_drawn_over(
        self, client: TestClient, venue: StubHistory
    ) -> None:
        """The reason the trim happens after the lines are computed.

        An EMA-50 over the last thirty bars would be an EMA-50 that started
        thirty bars ago, whose first values are the seed rather than the
        average. The line a rule would trade on is the one computed over
        everything held and then cut to what is on screen.
        """
        venue.history = _candles()

        cut = client.get(
            "/api/chart/shark/BTCUSDT",
            params={"interval": "1h", "bars": 30, "indicators": "ema:50"},
        ).json()
        whole = client.get(
            "/api/chart/shark/BTCUSDT", params={"interval": "1h", "indicators": "ema:50"}
        ).json()

        assert len(cut["lines"][0]["values"]) == 30
        assert cut["lines"][0]["values"] == whole["lines"][0]["values"][-30:]
        # and nothing is warming up inside the visible window
        assert all(v is not None for v in cut["lines"][0]["values"])

"""Futures OI for the index chart's pane."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api.app import app
from api.deps import get_broker
from broker.fyers.adapter import parse_oi_bars
from broker.models import OiBar


def test_oi_is_the_seventh_column() -> None:
    raw = {
        "s": "ok",
        "candles": [[1767312000, 26306.0, 26480.0, 26306.0, 26455.4, 4165915, 13702000]],
    }
    [bar] = parse_oi_bars(raw)
    assert bar.close == 26455.4
    assert bar.oi == 13702000
    assert bar.timestamp == datetime(2026, 1, 2, tzinfo=UTC)


def test_a_history_without_oi_gives_nothing_rather_than_volume() -> None:
    raw = {"s": "ok", "candles": [[1767312000, 1.0, 1.0, 1.0, 1.0, 5]]}
    assert parse_oi_bars(raw) == []


class OiBroker:
    """Answers OI history, and records the spans it was asked for."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, date, date]] = []

    def get_history_oi(
        self, symbol: str, resolution: str, date_from: date, date_to: date
    ) -> list[OiBar]:
        self.asked.append((resolution, date_from, date_to))
        at = datetime.combine(date_from, datetime.min.time(), tzinfo=UTC)
        return [OiBar(timestamp=at, close=100.0, oi=1000.0)]


@pytest.fixture
def oi_client() -> Iterator[tuple[TestClient, OiBroker]]:
    broker = OiBroker()
    app.dependency_overrides[get_broker] = lambda: broker
    try:
        yield TestClient(app), broker
    finally:
        app.dependency_overrides.clear()


def test_a_long_daily_window_is_asked_for_in_spans_fyers_accepts(
    oi_client: tuple[TestClient, OiBroker],
) -> None:
    client, broker = oi_client
    response = client.get(
        "/api/futures-oi/NSE:NIFTY26OCTFUT", params={"interval": "1w", "days": 1200}
    )
    assert response.status_code == 200
    assert all(r == "D" for r, _, _ in broker.asked)
    assert all(to - frm < timedelta(days=360) for _, frm, to in broker.asked)
    assert len(broker.asked) == 4
    points = response.json()
    assert [p["at"] for p in points] == sorted(p["at"] for p in points)


def test_an_unknown_interval_is_refused(oi_client: tuple[TestClient, OiBroker]) -> None:
    client, _ = oi_client
    response = client.get("/api/futures-oi/NSE:NIFTY26OCTFUT", params={"interval": "3m"})
    assert response.status_code == 400

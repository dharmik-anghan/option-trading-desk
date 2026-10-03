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
        self.symbols: list[str] = []

    def get_history_oi(
        self, symbol: str, resolution: str, date_from: date, date_to: date
    ) -> list[OiBar]:
        self.asked.append((resolution, date_from, date_to))
        self.symbols.append(symbol)
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
    # four spans for each of the near, next and far months
    assert len(broker.asked) == 12
    assert sorted(set(broker.symbols)) == [
        "NSE:NIFTY26DECFUT",
        "NSE:NIFTY26NOVFUT",
        "NSE:NIFTY26OCTFUT",
    ]
    points = response.json()
    assert [p["at"] for p in points] == sorted(p["at"] for p in points)


def test_an_unknown_interval_is_refused(oi_client: tuple[TestClient, OiBroker]) -> None:
    client, _ = oi_client
    response = client.get("/api/futures-oi/NSE:NIFTY26OCTFUT", params={"interval": "3m"})
    assert response.status_code == 400


def _bar(day: int, close: float, oi: float) -> OiBar:
    return OiBar(timestamp=datetime(2026, 9, day, tzinfo=UTC), close=close, oi=oi)


class Curve:
    """Three continuous series shaped like expiry week, 24-30 Sep 2026 (lakh)."""

    SERIES = {
        "NSE:NIFTY26OCTFUT": [
            _bar(24, 23108.6, 140.23),
            _bar(29, 22687.8, 66.23),
            _bar(30, 22707.3, 180.15),
        ],
        "NSE:NIFTY26NOVFUT": [_bar(24, 0, 81.60), _bar(29, 0, 171.35), _bar(30, 0, 21.16)],
        "NSE:NIFTY26DECFUT": [_bar(24, 0, 12.18), _bar(29, 0, 19.99), _bar(30, 0, 0.89)],
    }

    def get_history_oi(
        self, symbol: str, resolution: str, date_from: date, date_to: date
    ) -> list[OiBar]:
        return [b for b in self.SERIES[symbol] if date_from <= b.timestamp.date() <= date_to]


def test_oi_is_summed_across_the_curve_and_the_expiry_is_flagged() -> None:
    app.dependency_overrides[get_broker] = lambda: Curve()
    try:
        response = TestClient(app).get(
            "/api/futures-oi/NSE:NIFTY26OCTFUT", params={"interval": "1d", "days": 1500}
        )
    finally:
        app.dependency_overrides.clear()
    points = response.json()
    # Near month alone fell 74 L into expiry; the curve as a whole rose 23.6 L.
    assert [round(p["oi"], 2) for p in points] == [234.01, 257.57, 202.2]
    assert [p["roll"] for p in points] == [False, False, True]
    assert points[0]["close"] == 23108.6


def test_the_month_after_december_is_january_next_year() -> None:
    from broker.fyers.symbols import later_future

    assert later_future("NSE:NIFTY26DECFUT", 1) == "NSE:NIFTY27JANFUT"
    assert later_future("NSE:NIFTY26NOVFUT", 2) == "NSE:NIFTY27JANFUT"
    assert later_future("NSE:NIFTY26O0623100CE", 1) is None

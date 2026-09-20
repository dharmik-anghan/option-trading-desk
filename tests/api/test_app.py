from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import app
from api.dependencies import get_broker, get_db_path
from broker.fake import FakeBroker
from broker.models import Position
from storage.db import connect, init_schema
from storage.portfolio_repo import save_portfolio_snapshot


@pytest.fixture
def fake_broker() -> FakeBroker:
    return FakeBroker(
        underlying_ltp=100.0,
        positions=[
            Position(
                symbol="X-100-CE",
                net_quantity=-1,
                average_price=5.0,
                ltp=4.0,
                unrealized_pnl=100.0,
                product_type="MARGIN",
            )
        ],
        realized_pnl=50.0,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def client(fake_broker: FakeBroker, db_path: Path) -> Iterator[TestClient]:
    app.dependency_overrides[get_broker] = lambda: fake_broker
    app.dependency_overrides[get_db_path] = lambda: db_path
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health() -> None:
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_portfolio_endpoint_returns_positions_and_pnl(client: TestClient) -> None:
    response = client.get("/api/portfolio")

    assert response.status_code == 200
    data = response.json()
    assert len(data["positions"]) == 1
    assert data["positions"][0]["symbol"] == "X-100-CE"
    assert data["realized_pnl"] == 50.0
    assert data["unrealized_pnl"] == 100.0
    assert data["total_pnl"] == 150.0


def test_option_chain_endpoint_returns_chain(client: TestClient) -> None:
    response = client.get("/api/option-chain/NSE:NIFTY50-INDEX")

    assert response.status_code == 200
    data = response.json()
    assert data["underlying_symbol"] == "NSE:NIFTY50-INDEX"
    assert len(data["rows"]) > 0


def test_strategy_endpoint_returns_signal(client: TestClient) -> None:
    response = client.get("/api/strategies/iron_condor?symbol=NSE:NIFTY50-INDEX")

    assert response.status_code == 200
    data = response.json()
    assert data["strategy"] == "iron_condor"
    assert len(data["legs"]) == 4
    assert isinstance(data["max_loss"], float)


def test_strategy_endpoint_serializes_unbounded_risk_as_json_null(client: TestClient) -> None:
    # A naked short strangle has unbounded max loss (math.inf/-math.inf in
    # Python). Python's json module happily emits the literal token
    # `Infinity`, but that is NOT valid JSON - a browser's JSON.parse
    # rejects it outright. Must serialize as `null`, not a raw float.
    response = client.get("/api/strategies/short_strangle?symbol=NSE:NIFTY50-INDEX")

    assert response.status_code == 200
    assert "Infinity" not in response.text
    data = response.json()
    assert data["max_loss"] is None


def test_strategy_endpoint_rejects_unknown_strategy(client: TestClient) -> None:
    response = client.get("/api/strategies/not_a_real_strategy?symbol=NSE:NIFTY50-INDEX")

    assert response.status_code == 404


def test_strategy_endpoint_includes_pre_trade_checks(client: TestClient) -> None:
    response = client.get("/api/strategies/iron_condor?symbol=NSE:NIFTY50-INDEX")

    assert response.status_code == 200
    data = response.json()
    assert data["can_place"] is True
    assert len(data["pre_trade_checks"]) >= 1
    assert all("passed" in c and "reason" in c for c in data["pre_trade_checks"])


def test_strategy_endpoint_cannot_place_when_funds_insufficient() -> None:
    broker = FakeBroker(underlying_ltp=100.0, available_balance=1.0)
    app.dependency_overrides[get_broker] = lambda: broker
    try:
        response = TestClient(app).get(
            "/api/strategies/iron_condor?symbol=NSE:NIFTY50-INDEX"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["can_place"] is False


def test_place_order_succeeds_and_reaches_the_broker(
    client: TestClient, fake_broker: FakeBroker
) -> None:
    response = client.post(
        "/api/orders/place",
        json={"strategy": "iron_condor", "symbol": "NSE:NIFTY50-INDEX", "quantity": 1},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["orders"]) == 4
    assert len(fake_broker.placed_orders) == 4


def test_place_order_blocked_when_pre_trade_checks_fail() -> None:
    broker = FakeBroker(underlying_ltp=100.0, available_balance=1.0)
    app.dependency_overrides[get_broker] = lambda: broker
    try:
        response = TestClient(app).post(
            "/api/orders/place",
            json={"strategy": "iron_condor", "symbol": "NSE:NIFTY50-INDEX", "quantity": 1},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert len(broker.placed_orders) == 0


def test_place_order_rejects_unknown_strategy(client: TestClient) -> None:
    response = client.post(
        "/api/orders/place",
        json={"strategy": "not_real", "symbol": "NSE:NIFTY50-INDEX", "quantity": 1},
    )

    assert response.status_code == 404


def test_portfolio_history_endpoint_returns_saved_snapshots(
    client: TestClient, db_path: Path
) -> None:
    conn = connect(str(db_path))
    init_schema(conn)
    save_portfolio_snapshot(
        conn,
        positions=[],
        realized_pnl=100.0,
        unrealized_pnl=20.0,
        fetched_at=datetime(2026, 9, 20, 10, 0, tzinfo=UTC),
    )
    conn.close()

    response = client.get("/api/portfolio/history")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["total_pnl"] == 120.0


def test_portfolio_history_endpoint_creates_db_if_missing(
    client: TestClient, db_path: Path
) -> None:
    assert not db_path.exists()

    response = client.get("/api/portfolio/history")

    assert response.status_code == 200
    assert response.json() == []

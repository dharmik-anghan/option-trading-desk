from __future__ import annotations

import math
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


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # reports whether broker reads are currently being served over a rate limit
    assert body["rate_limited"] is False


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
    assert len(data["payoff_curve"]) > 0
    assert all("spot" in p and "payoff" in p for p in data["payoff_curve"])


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
    assert isinstance(data["basket_id"], int)


def test_place_order_creates_a_basket_with_the_placed_legs(client: TestClient) -> None:
    place_response = client.post(
        "/api/orders/place",
        json={
            "strategy": "iron_condor",
            "symbol": "NSE:NIFTY50-INDEX",
            "quantity": 1,
            "basket_name": "My 45 DTE IC",
        },
    )
    basket_id = place_response.json()["basket_id"]

    basket_response = client.get(f"/api/baskets/{basket_id}")

    assert basket_response.status_code == 200
    basket = basket_response.json()
    assert basket["name"] == "My 45 DTE IC"
    assert basket["strategy"] == "iron_condor"
    assert len(basket["legs"]) == 4
    assert all(leg["is_open"] for leg in basket["legs"])


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


def _create_basket_payload() -> dict[str, object]:
    return {
        "name": "Manual basket",
        "strategy": "iron_condor",
        "underlying_symbol": "NSE:NIFTY50-INDEX",
        "legs": [
            {
                "symbol": "X-90-PE",
                "option_type": "PE",
                "strike": 90,
                "side": "BUY",
                "quantity": 1,
                "entry_price": 1.0,
            },
            {
                "symbol": "X-95-PE",
                "option_type": "PE",
                "strike": 95,
                "side": "SELL",
                "quantity": 1,
                "entry_price": 3.0,
            },
        ],
    }


def test_create_basket_endpoint(client: TestClient) -> None:
    response = client.post("/api/baskets", json=_create_basket_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Manual basket"
    assert len(data["legs"]) == 2
    assert math.isfinite(data["max_profit"])
    assert len(data["payoff_curve"]) > 0


def test_list_baskets_endpoint(client: TestClient) -> None:
    client.post("/api/baskets", json=_create_basket_payload())

    response = client.get("/api/baskets")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_get_basket_endpoint_404_for_unknown(client: TestClient) -> None:
    response = client.get("/api/baskets/999")

    assert response.status_code == 404


def test_close_leg_endpoint_updates_basket_payoff(client: TestClient) -> None:
    created = client.post("/api/baskets", json=_create_basket_payload()).json()
    leg_to_close = created["legs"][0]
    basket_id = created["id"]

    response = client.post(
        f"/api/baskets/{basket_id}/legs/{leg_to_close['id']}/close",
        json={"exit_price": 0.5},
    )

    assert response.status_code == 200
    data = response.json()
    closed_leg = next(leg for leg in data["legs"] if leg["id"] == leg_to_close["id"])
    assert closed_leg["is_open"] is False
    assert closed_leg["exit_price"] == pytest.approx(0.5)


def test_close_leg_endpoint_empty_curve_when_fully_closed(client: TestClient) -> None:
    payload = {
        "name": "Single leg",
        "strategy": "iron_condor",
        "underlying_symbol": "NSE:NIFTY50-INDEX",
        "legs": [
            {
                "symbol": "X-90-PE",
                "option_type": "PE",
                "strike": 90,
                "side": "SELL",
                "quantity": 1,
                "entry_price": 3.0,
            }
        ],
    }
    created = client.post("/api/baskets", json=payload).json()
    leg = created["legs"][0]

    response = client.post(
        f"/api/baskets/{created['id']}/legs/{leg['id']}/close", json={"exit_price": 1.0}
    )

    assert response.status_code == 200
    assert response.json()["payoff_curve"] == []


@pytest.fixture
def market_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the exchange is trading, whatever day the suite runs on."""
    import api.app as app_module

    monkeypatch.setattr(app_module, "in_session", lambda *_a, **_k: True)
    app_module._last_snapshot = None


def test_fetching_the_portfolio_records_a_snapshot(
    client: TestClient, market_open: None
) -> None:
    client.get("/api/portfolio")

    history = client.get("/api/portfolio/history?days=7").json()
    assert len(history) == 1


def test_snapshots_are_throttled_rather_than_one_per_poll(
    client: TestClient, market_open: None
) -> None:
    """The portfolio is polled every few seconds; history is not needed that often."""
    for _ in range(5):
        client.get("/api/portfolio")

    history = client.get("/api/portfolio/history?days=7").json()
    assert len(history) == 1


def test_nothing_is_recorded_while_the_exchange_is_shut(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Overnight, at weekends and on holidays the P&L cannot move, so a
    snapshot then is a duplicate of the close."""
    import api.app as app_module

    monkeypatch.setattr(app_module, "in_session", lambda *_a, **_k: False)
    app_module._last_snapshot = None

    for _ in range(3):
        assert client.get("/api/portfolio").status_code == 200

    assert client.get("/api/portfolio/history?days=7").json() == []


def test_a_snapshot_failure_does_not_fail_the_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, market_open: None
) -> None:
    import sqlite3

    import api.app as app_module

    def boom(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("disk is full")

    monkeypatch.setattr(app_module, "save_portfolio_snapshot", boom)

    # the P&L on screen matters more than the history behind it
    response = client.get("/api/portfolio")
    assert response.status_code == 200
    assert "total_pnl" in response.json()


def test_a_calendar_spread_does_not_report_a_single_expiry_payoff(
    client: TestClient,
) -> None:
    """Intrinsic value at one expiry prices a calendar's far leg at zero, so the
    whole net debit comes out as a certain loss. Better to show nothing."""
    created = client.post(
        "/api/baskets",
        json={
            "name": "Oct/Nov calendar",
            "strategy": "Calendar spread",
            "underlying_symbol": "NSE:NIFTY50-INDEX",
            "legs": [
                {
                    "symbol": "NSE:NIFTY26OCT23100CE",
                    "option_type": "CE",
                    "strike": 23100,
                    "side": "SELL",
                    "quantity": 75,
                    "entry_price": 402.15,
                },
                {
                    "symbol": "NSE:NIFTY26NOV23100CE",
                    "option_type": "CE",
                    "strike": 23100,
                    "side": "BUY",
                    "quantity": 75,
                    "entry_price": 520.0,
                },
            ],
        },
    ).json()

    assert created["single_expiry"] is False
    assert created["payoff_curve"] == []
    assert created["breakevens"] == []
    # and not the net debit dressed up as a worst case
    assert created["max_loss"] == 0.0


def test_a_vertical_in_one_expiry_still_gets_its_payoff(client: TestClient) -> None:
    created = client.post(
        "/api/baskets",
        json={
            "name": "Bear call",
            "strategy": "Whatever I want to call it",
            "underlying_symbol": "NSE:NIFTY50-INDEX",
            "legs": [
                {
                    "symbol": "NSE:NIFTY26OCT23100CE",
                    "option_type": "CE",
                    "strike": 23100,
                    "side": "SELL",
                    "quantity": 75,
                    "entry_price": 402.15,
                },
                {
                    "symbol": "NSE:NIFTY26OCT23500CE",
                    "option_type": "CE",
                    "strike": 23500,
                    "side": "BUY",
                    "quantity": 75,
                    "entry_price": 212.10,
                },
            ],
        },
    ).json()

    assert created["single_expiry"] is True
    assert created["payoff_curve"]
    # the structure label is stored as given, not matched against a known list
    assert created["strategy"] == "Whatever I want to call it"

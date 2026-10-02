"""Structures following the broker, through the API.

Fills from a fake broker whose `place_order` fails: every endpoint here may read
the broker and write the desk's database, and nothing else.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from broker.fake import FakeBroker
from broker.models import Fill, OrderRequest, OrderResult, Position
from storage.basket_repo import NewBasketLeg, create_basket
from storage.db import connect, init_schema

IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist(minutes_ago: int) -> datetime:
    return datetime.now(IST).replace(second=0, microsecond=0) - timedelta(minutes=minutes_ago)


def _fill(fid: str, strike: str, side: str, price: float, at: datetime) -> Fill:
    return Fill(fill_id=fid, order_id=fid, symbol=f"NSE:NIFTY26OCT{strike}", side=side,  # type: ignore[arg-type]
                quantity=65, price=price, at=at)


def _pos(strike: str, qty: float) -> Position:
    return Position(symbol=f"NSE:NIFTY26OCT{strike}", net_quantity=qty, average_price=0, ltp=0,
                    unrealized_pnl=0, product_type="MARGIN")


@pytest.fixture
def condor(db_path: Path) -> int:
    conn = connect(str(db_path))
    init_schema(conn)
    legs = [
        NewBasketLeg(symbol=f"NSE:NIFTY26OCT{k}{t}", option_type=t, strike=k, side=s,  # type: ignore[arg-type]
                     quantity=65, entry_price=p)
        for k, t, s, p in [(22900, "PE", "SELL", 225.35), (22500, "PE", "BUY", 137.10),
                           (23800, "CE", "SELL", 190.30), (24200, "CE", "BUY", 95.05)]
    ]
    bid = create_basket(conn, "27 Oct - Iron Condor", "Iron condor", "NSE:NIFTY50-INDEX", legs,
                        created_at=datetime.now(UTC) - timedelta(days=3))
    conn.close()
    return bid


@pytest.fixture
def rolled(fake_broker: FakeBroker, monkeypatch: pytest.MonkeyPatch) -> FakeBroker:
    """Today's roll: call spread bought back, a lower one sold."""
    fake_broker.fills = [
        _fill("A", "23800CE", "BUY", 39.40, _now_ist(30)),
        _fill("B", "24200CE", "SELL", 19.25, _now_ist(30)),
        _fill("C", "23100CE", "SELL", 176.10, _now_ist(29)),
        _fill("D", "23500CE", "BUY", 74.70, _now_ist(29)),
    ]
    fake_broker.positions = [_pos("22900PE", -65), _pos("22500PE", 65), _pos("23100CE", -65),
                             _pos("23500CE", 65)]

    def refuse(order: OrderRequest) -> OrderResult:
        raise AssertionError("an endpoint tried to place an order")

    monkeypatch.setattr(fake_broker, "place_order", refuse)
    return fake_broker


def test_sync_closes_the_legs_and_holds_the_new_ones_for_you(
    client: TestClient, condor: int, rolled: FakeBroker
) -> None:
    body = client.post("/api/baskets/sync").json()
    assert sorted(round(c["realized"], 2) for c in body["closed"]) == [-4927.0, 9808.5]
    assert [p["symbol"][-7:] for p in body["pending"]] == ["23100CE", "23500CE"]
    assert body["pending"][0]["suggestion"]["basket_id"] == condor

    basket = client.get(f"/api/baskets/{condor}").json()
    assert basket["realized"] == pytest.approx(4881.5)
    assert basket["closed_at"] is None


def test_assigning_then_reading_the_history(
    client: TestClient, condor: int, rolled: FakeBroker
) -> None:
    pending = client.post("/api/baskets/sync").json()["pending"]
    basket = client.post(
        "/api/baskets/fills/assign",
        json={"fill_ids": [p["fill_id"] for p in pending], "basket_id": condor},
    ).json()
    assert sorted(leg["symbol"][-7:] for leg in basket["legs"] if leg["is_open"]) == [
        "22500PE", "22900PE", "23100CE", "23500CE"]
    assert client.get("/api/baskets/fills/pending").json() == []

    moments = client.get(f"/api/baskets/{condor}/history").json()
    assert [m["kind"] for m in moments] == ["opened", "adjusted"]
    assert moments[1]["realized"] == pytest.approx(4881.5)


def test_a_second_sync_changes_nothing(client: TestClient, condor: int, rolled: FakeBroker) -> None:
    client.post("/api/baskets/sync")
    again = client.post("/api/baskets/sync").json()
    assert again["closed"] == [] and again["already_seen"] == 4


def test_a_leg_can_be_added_by_hand(client: TestClient, condor: int) -> None:
    basket = client.post(
        f"/api/baskets/{condor}/legs",
        json={"symbol": "NSE:NIFTY26OCT23100CE", "side": "SELL", "quantity": 65,
              "entry_price": 176.1, "entry_at": "2026-09-29T10:36:49"},
    ).json()
    added = [leg for leg in basket["legs"] if leg["symbol"].endswith("23100CE")]
    assert added[0]["strike"] == 23100 and added[0]["option_type"] == "CE"
    assert added[0]["entry_at"].startswith("2026-09-29T10:36:49+05:30")


def test_a_symbol_that_is_not_an_option_is_refused(client: TestClient, condor: int) -> None:
    r = client.post(f"/api/baskets/{condor}/legs",
                    json={"symbol": "NSE:SBIN-EQ", "side": "BUY", "quantity": 1,
                          "entry_price": 1.0})
    assert r.status_code == 422

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from storage.basket_repo import (
    NewBasketLeg,
    close_leg,
    create_basket,
    delete_basket,
    delete_leg,
    get_basket,
    list_baskets,
)
from storage.db import connect, init_schema


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = connect(":memory:")
    init_schema(connection)
    return connection


def _iron_condor_legs() -> list[NewBasketLeg]:
    return [
        NewBasketLeg(
            symbol="X-90-PE", option_type="PE", strike=90, side="BUY", quantity=1, entry_price=1.0
        ),
        NewBasketLeg(
            symbol="X-95-PE", option_type="PE", strike=95, side="SELL", quantity=1, entry_price=3.0
        ),
        NewBasketLeg(
            symbol="X-105-CE",
            option_type="CE",
            strike=105,
            side="SELL",
            quantity=1,
            entry_price=3.0,
        ),
        NewBasketLeg(
            symbol="X-110-CE", option_type="CE", strike=110, side="BUY", quantity=1, entry_price=1.0
        ),
    ]


def test_create_and_get_basket_round_trips(conn: sqlite3.Connection) -> None:
    created_at = datetime(2026, 9, 21, 9, 15, tzinfo=UTC)

    basket_id = create_basket(
        conn,
        name="45 DTE Iron Condor",
        strategy="iron_condor",
        underlying_symbol="NSE:NIFTY50-INDEX",
        legs=_iron_condor_legs(),
        created_at=created_at,
        stop_loss=-2000.0,
    )
    basket = get_basket(conn, basket_id)

    assert basket is not None
    assert basket.name == "45 DTE Iron Condor"
    assert basket.strategy == "iron_condor"
    assert basket.underlying_symbol == "NSE:NIFTY50-INDEX"
    assert basket.created_at == created_at
    assert basket.stop_loss == -2000.0
    assert len(basket.legs) == 4
    assert all(leg.is_open for leg in basket.legs)


def test_get_basket_returns_none_for_unknown_id(conn: sqlite3.Connection) -> None:
    assert get_basket(conn, 999) is None


def test_list_baskets_returns_all_created(conn: sqlite3.Connection) -> None:
    create_basket(
        conn,
        name="A",
        strategy="iron_condor",
        underlying_symbol="NSE:NIFTY50-INDEX",
        legs=_iron_condor_legs(),
        created_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    create_basket(
        conn,
        name="B",
        strategy="short_strangle",
        underlying_symbol="NSE:BANKNIFTY-INDEX",
        legs=_iron_condor_legs(),
        created_at=datetime(2026, 9, 21, tzinfo=UTC),
    )

    baskets = list_baskets(conn)

    assert {b.name for b in baskets} == {"A", "B"}


def test_close_leg_records_exit(conn: sqlite3.Connection) -> None:
    basket_id = create_basket(
        conn,
        name="A",
        strategy="iron_condor",
        underlying_symbol="NSE:NIFTY50-INDEX",
        legs=_iron_condor_legs(),
        created_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    basket = get_basket(conn, basket_id)
    assert basket is not None
    leg_to_close = basket.legs[0]
    exit_at = datetime(2026, 9, 25, tzinfo=UTC)

    close_leg(conn, leg_to_close.id, exit_price=0.5, exit_at=exit_at)
    updated = get_basket(conn, basket_id)

    assert updated is not None
    closed = next(leg for leg in updated.legs if leg.id == leg_to_close.id)
    assert closed.is_open is False
    assert closed.exit_price == pytest.approx(0.5)
    assert closed.exit_at == exit_at
    still_open = [leg for leg in updated.legs if leg.id != leg_to_close.id]
    assert all(leg.is_open for leg in still_open)


def _make(conn: sqlite3.Connection, name: str = "condor") -> int:
    return create_basket(
        conn,
        name=name,
        strategy="iron_condor",
        underlying_symbol="X",
        legs=_iron_condor_legs(),
        created_at=datetime.now(UTC),
    )


def test_delete_basket_removes_basket_and_its_legs(conn: sqlite3.Connection) -> None:
    basket_id = _make(conn)

    assert delete_basket(conn, basket_id) is True

    assert get_basket(conn, basket_id) is None
    orphans = conn.execute(
        "SELECT COUNT(*) FROM basket_leg WHERE basket_id = ?", (basket_id,)
    ).fetchone()[0]
    assert orphans == 0


def test_delete_basket_is_false_for_unknown_id(conn: sqlite3.Connection) -> None:
    assert delete_basket(conn, 4242) is False


def test_delete_basket_leaves_other_baskets_alone(conn: sqlite3.Connection) -> None:
    keep = _make(conn, "keep")
    drop = _make(conn, "drop")

    delete_basket(conn, drop)

    remaining = list_baskets(conn)
    assert [b.id for b in remaining] == [keep]
    assert len(remaining[0].legs) == 4


def test_delete_leg_drops_only_that_leg(conn: sqlite3.Connection) -> None:
    basket_id = _make(conn)
    basket = get_basket(conn, basket_id)
    assert basket is not None
    victim = basket.legs[1].id

    assert delete_leg(conn, basket_id, victim) is True

    after = get_basket(conn, basket_id)
    assert after is not None
    assert [leg.id for leg in after.legs] == [
        leg.id for leg in basket.legs if leg.id != victim
    ]


def test_delete_leg_refuses_a_leg_from_another_basket(conn: sqlite3.Connection) -> None:
    mine = _make(conn, "mine")
    theirs = _make(conn, "theirs")
    other = get_basket(conn, theirs)
    assert other is not None

    # a stale id from another basket must not delete across the boundary
    assert delete_leg(conn, mine, other.legs[0].id) is False

    still_there = get_basket(conn, theirs)
    assert still_there is not None
    assert len(still_there.legs) == 4


def test_removing_the_last_leg_deletes_the_empty_basket(conn: sqlite3.Connection) -> None:
    basket_id = _make(conn)
    basket = get_basket(conn, basket_id)
    assert basket is not None

    for leg in basket.legs:
        assert delete_leg(conn, basket_id, leg.id) is True

    assert get_basket(conn, basket_id) is None


def test_delete_leg_is_distinct_from_closing_one(conn: sqlite3.Connection) -> None:
    basket_id = _make(conn)
    basket = get_basket(conn, basket_id)
    assert basket is not None

    close_leg(conn, basket.legs[0].id, exit_price=2.0, exit_at=datetime.now(UTC))

    after = get_basket(conn, basket_id)
    assert after is not None
    # closing keeps the leg on the basket; deleting would have removed it
    assert len(after.legs) == 4
    assert after.legs[0].is_open is False
    assert after.legs[0].exit_price == 2.0

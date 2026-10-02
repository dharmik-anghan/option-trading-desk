"""Reconciling structures from broker fills.

The main scenario is the one that prompted this, figures and times as they
happened on 29 Sep 2026: an Oct iron condor recorded on the 26th, its call
spread bought back at 10:36, and a lower call spread sold a few seconds later.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from broker.fake import FakeBroker
from broker.fyers import SYMBOLS
from broker.fyers.symbols import parse_contract
from broker.models import Fill, OrderRequest, OrderResult, Position
from execution.basket_history import closed_at, history, realized
from execution.broker_sync import assign, ignore, sync
from storage import fill_repo
from storage.basket_repo import NewBasketLeg, create_basket, get_basket
from storage.db import connect, init_schema

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
ADOPTED = datetime(2026, 9, 26, 6, 25, tzinfo=UTC)


def ist(day: int, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(2026, 9, day, hh, mm, ss, tzinfo=IST)


def fill(fid: str, symbol: str, side: str, price: float, at: datetime, qty: int = 65) -> Fill:
    return Fill(fill_id=fid, order_id=fid.split(":")[0], symbol=f"NSE:NIFTY26OCT{symbol}",
                side=side, quantity=qty, price=price, at=at)  # type: ignore[arg-type]


def pos(symbol: str, qty: float) -> Position:
    return Position(symbol=f"NSE:NIFTY26OCT{symbol}", net_quantity=qty, average_price=0,
                    ltp=0, unrealized_pnl=0, product_type="MARGIN")


class NoOrders(FakeBroker):
    """A broker whose only write fails loudly: the sync must never reach it."""

    def place_order(self, order: OrderRequest) -> OrderResult:
        raise AssertionError("the sync tried to place an order")


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = connect(":memory:")
    init_schema(c)
    return c


def _condor(conn: sqlite3.Connection) -> int:
    def leg(strike: int, kind: str, side: str, price: float) -> NewBasketLeg:
        return NewBasketLeg(symbol=f"NSE:NIFTY26OCT{strike}{kind}", option_type=kind,  # type: ignore[arg-type]
                            strike=strike, side=side, quantity=65, entry_price=price)  # type: ignore[arg-type]

    return create_basket(
        conn, "27 Oct - Iron Condor", "Iron condor", "NSE:NIFTY50-INDEX",
        [leg(22900, "PE", "SELL", 225.35), leg(22500, "PE", "BUY", 137.10),
         leg(23800, "CE", "SELL", 190.30), leg(24200, "CE", "BUY", 95.05)],
        created_at=ADOPTED,
    )


TODAY = [
    fill("A:1", "23800CE", "BUY", 39.40, ist(29, 10, 36, 4)),
    fill("B:1", "24200CE", "SELL", 19.25, ist(29, 10, 36, 7)),
    fill("C:1", "23100CE", "SELL", 176.10, ist(29, 10, 36, 49)),
    fill("D:1", "23500CE", "BUY", 74.70, ist(29, 10, 36, 49)),
]
HELD_NOW = [pos("22900PE", -65), pos("22500PE", 65), pos("23100CE", -65), pos("23500CE", 65)]


def _run(conn: sqlite3.Connection, fills: list[Fill], held: list[Position]):  # type: ignore[no-untyped-def]
    broker = NoOrders(fills=fills, positions=held)
    return sync(
        conn, broker, codec=SYMBOLS, since=date(2026, 9, 22), until=date(2026, 9, 29), now=NOW
    )


def test_the_call_spread_closed_at_the_broker_closes_on_the_desk(conn: sqlite3.Connection) -> None:
    bid = _condor(conn)
    report = _run(conn, TODAY, HELD_NOW)

    assert sorted((a.fill.symbol[-7:], round(a.realized, 2)) for a in report.closed) == [
        ("23800CE", 9808.5), ("24200CE", -4927.0)]
    basket = get_basket(conn, bid)
    assert basket is not None
    closed = {leg.symbol[-7:]: leg for leg in basket.legs if not leg.is_open}
    assert closed["23800CE"].exit_price == 39.40
    assert closed["23800CE"].exit_at == ist(29, 10, 36, 4)
    assert realized(basket) == pytest.approx(4881.5)  # matches Fyers' 9,808.50 - 4,927


def test_the_new_spread_waits_with_the_condor_suggested(conn: sqlite3.Connection) -> None:
    bid = _condor(conn)
    report = _run(conn, TODAY, HELD_NOW)
    assert [p.symbol[-7:] for p in report.pending] == ["23100CE", "23500CE"]
    for p in report.pending:
        assert p.suggestion is not None
        assert p.suggestion.basket_id == bid
        assert "adjustment" in p.suggestion.why
    assert report.unexplained == []


def test_nothing_is_put_into_a_structure_without_being_asked(conn: sqlite3.Connection) -> None:
    bid = _condor(conn)
    _run(conn, TODAY, HELD_NOW)
    basket = get_basket(conn, bid)
    assert basket is not None
    assert {leg.symbol[-7:] for leg in basket.legs} == {"22900PE", "22500PE", "23800CE", "24200CE"}


def test_assigning_the_new_spread_makes_it_legs_of_the_condor(conn: sqlite3.Connection) -> None:
    bid = _condor(conn)
    report = _run(conn, TODAY, HELD_NOW)
    assign(conn, [p.fill_id for p in report.pending], codec=SYMBOLS, basket_id=bid, now=NOW)

    basket = get_basket(conn, bid)
    assert basket is not None
    open_legs = sorted((leg.symbol[-7:], leg.side, leg.entry_price) for leg in basket.legs
                       if leg.is_open)
    assert open_legs == [("22500PE", "BUY", 137.10), ("22900PE", "SELL", 225.35),
                         ("23100CE", "SELL", 176.10), ("23500CE", "BUY", 74.70)]
    assert fill_repo.with_status(conn, "pending") == []


def test_the_history_reads_opened_then_adjusted(conn: sqlite3.Connection) -> None:
    bid = _condor(conn)
    report = _run(conn, TODAY, HELD_NOW)
    assign(conn, [p.fill_id for p in report.pending], codec=SYMBOLS, basket_id=bid, now=NOW)
    basket = get_basket(conn, bid)
    assert basket is not None

    moments = history(basket)
    assert [m.kind for m in moments] == ["opened", "adjusted"]
    adjusted = moments[1]
    assert adjusted.realized == pytest.approx(4881.5)
    # Sold 23100 at 176.10, bought 23500 at 74.70: 101.40 x 65 of new credit.
    assert adjusted.premium == pytest.approx(101.40 * 65)
    assert closed_at(basket) is None


def test_running_it_twice_applies_nothing_twice(conn: sqlite3.Connection) -> None:
    bid = _condor(conn)
    _run(conn, TODAY, HELD_NOW)
    again = _run(conn, TODAY, HELD_NOW)
    assert again.closed == [] and again.already_seen == 4
    basket = get_basket(conn, bid)
    assert basket is not None
    assert len(basket.legs) == 4


def test_the_trades_that_built_an_adopted_leg_are_recognised_not_repeated(
    conn: sqlite3.Connection,
) -> None:
    # The put spread was sold on 15 Sep and adopted on the 26th. Those fills
    # arrive in trade history, and are the legs already on the desk.
    _condor(conn)
    old = [fill("E:1", "22900PE", "SELL", 225.35, ist(15, 15, 12, 34)),
           fill("F:1", "22500PE", "BUY", 137.10, ist(15, 15, 12, 40))]
    broker = NoOrders(fills=old, positions=HELD_NOW)
    report = sync(
        conn, broker, codec=SYMBOLS, since=date(2026, 9, 1), until=date(2026, 9, 29), now=NOW
    )
    assert report.covered == 2 and report.pending == [] and report.closed == []


def test_a_round_trip_outside_any_structure_is_noted_and_left_alone(
    conn: sqlite3.Connection,
) -> None:
    _condor(conn)
    trip = [fill("G:1", "24000PE", "SELL", 50.0, ist(29, 9, 20)),
            fill("H:1", "24000PE", "BUY", 30.0, ist(29, 11, 0))]
    report = _run(conn, trip, HELD_NOW)
    assert report.outside == 2 and report.pending == []


def test_a_partial_close_splits_the_leg(conn: sqlite3.Connection) -> None:
    bid = create_basket(
        conn, "two lots", "Custom", "NSE:NIFTY50-INDEX",
        [NewBasketLeg(symbol="NSE:NIFTY26OCT23800CE", option_type="CE", strike=23800,
                      side="SELL", quantity=130, entry_price=190.0)],
        created_at=ADOPTED,
    )
    report = _run(conn, [fill("A:1", "23800CE", "BUY", 40.0, ist(29, 10, 0))],
                  [pos("23800CE", -65)])
    assert report.closed[0].quantity == 65
    basket = get_basket(conn, bid)
    assert basket is not None
    assert sorted((leg.quantity, leg.is_open) for leg in basket.legs) == [(65, False), (65, True)]
    assert [m.kind for m in history(basket)] == ["opened", "reduced"]


def test_a_leg_the_broker_no_longer_holds_is_reported_not_closed(
    conn: sqlite3.Connection,
) -> None:
    bid = _condor(conn)
    # No fills at all, and the broker holds no calls: the desk cannot know the
    # exit price, so it says so rather than inventing one.
    report = _run(conn, [], [pos("22900PE", -65), pos("22500PE", 65)])
    assert sorted(u.leg.symbol[-7:] for u in report.unexplained) == ["23800CE", "24200CE"]
    basket = get_basket(conn, bid)
    assert basket is not None and all(leg.is_open for leg in basket.legs)


def test_a_fully_closed_structure_knows_when_it_closed(conn: sqlite3.Connection) -> None:
    bid = create_basket(
        conn, "one leg", "Custom", "NSE:NIFTY50-INDEX",
        [NewBasketLeg(symbol="NSE:NIFTY26OCT23800CE", option_type="CE", strike=23800,
                      side="SELL", quantity=65, entry_price=190.0)],
        created_at=ADOPTED,
    )
    _run(conn, [fill("A:1", "23800CE", "BUY", 40.0, ist(29, 10, 0))], [])
    basket = get_basket(conn, bid)
    assert basket is not None
    assert closed_at(basket) == ist(29, 10, 0)
    assert [m.kind for m in history(basket)] == ["opened", "closed"]


def test_pending_fills_can_start_a_new_structure_or_be_ignored(conn: sqlite3.Connection) -> None:
    _condor(conn)
    report = _run(conn, TODAY, HELD_NOW)
    first, second = (p.fill_id for p in report.pending)
    new_id = assign(conn, [first], codec=SYMBOLS, new_name="Call spread", now=NOW)
    ignore(conn, [second])
    fresh = get_basket(conn, new_id)
    assert fresh is not None and [leg.symbol[-7:] for leg in fresh.legs] == ["23100CE"]
    assert fill_repo.with_status(conn, "ignored")[0].fill_id == second


def test_the_sync_never_places_an_order(conn: sqlite3.Connection) -> None:
    _condor(conn)
    broker = NoOrders(fills=TODAY, positions=HELD_NOW)
    sync(conn, broker, codec=SYMBOLS, since=date(2026, 9, 22), until=date(2026, 9, 29), now=NOW)
    assert broker.placed_orders == []


@pytest.mark.parametrize(
    ("symbol", "parsed"),
    [
        ("NSE:NIFTY26OCT23100CE", ("NSE:NIFTY26OCT", 23100.0, "CE")),
        ("NSE:NIFTY2692225000PE", ("NSE:NIFTY26922", 25000.0, "PE")),
        ("NSE:NIFTY26O0625000CE", ("NSE:NIFTY26O06", 25000.0, "CE")),
        ("NSE:BANKNIFTY26OCT52000PE", ("NSE:BANKNIFTY26OCT", 52000.0, "PE")),
        ("NSE:NIFTY26OCTFUT", None),
        ("NSE:SBIN-EQ", None),
    ],
)
def test_contract_symbols_are_read_into_series_strike_and_type(
    symbol: str, parsed: tuple[str, float, str] | None
) -> None:
    assert parse_contract(symbol) == parsed

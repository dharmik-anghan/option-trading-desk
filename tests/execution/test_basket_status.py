"""The core promise of the basket system: a strategy's payoff reflects P&L
already banked from legs you've since exited, not just what's still open.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from execution.basket_status import get_basket_payoff, leg_realized_pnl
from storage.basket_repo import Basket, BasketLeg


def _leg(
    symbol: str,
    option_type: str,
    strike: float,
    side: str,
    quantity: int,
    entry_price: float,
    exit_price: float | None = None,
    leg_id: int = 1,
) -> BasketLeg:
    return BasketLeg(
        id=leg_id,
        symbol=symbol,
        option_type=option_type,  # type: ignore[arg-type]
        strike=strike,
        side=side,  # type: ignore[arg-type]
        quantity=quantity,
        entry_price=entry_price,
        entry_at=datetime(2026, 9, 1, tzinfo=UTC),
        exit_price=exit_price,
        exit_at=datetime(2026, 9, 5, tzinfo=UTC) if exit_price is not None else None,
    )


def test_leg_realized_pnl_is_zero_for_open_leg() -> None:
    leg = _leg("X", "CE", 100, "BUY", 1, entry_price=5.0)

    assert leg_realized_pnl(leg) == 0.0


def test_leg_realized_pnl_for_closed_buy_leg() -> None:
    # Bought at 5, sold (closed) at 8 -> profit 3 per unit.
    leg = _leg("X", "CE", 100, "BUY", 2, entry_price=5.0, exit_price=8.0)

    assert leg_realized_pnl(leg) == pytest.approx(6.0)


def test_leg_realized_pnl_for_closed_sell_leg() -> None:
    # Sold at 8, bought back (closed) at 5 -> profit 3 per unit.
    leg = _leg("X", "CE", 100, "SELL", 2, entry_price=8.0, exit_price=5.0)

    assert leg_realized_pnl(leg) == pytest.approx(6.0)


def _basket(legs: list[BasketLeg]) -> Basket:
    return Basket(
        id=1,
        name="test",
        strategy="iron_condor",
        underlying_symbol="X",
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        stop_loss=None,
        profit_target=None,
        delta_limit=None,
        legs=legs,
    )


def test_basket_payoff_combines_open_payoff_with_closed_leg_realized_pnl() -> None:
    # A bull call spread (open) plus one already-closed leg that banked +50.
    basket = _basket(
        [
            _leg("A", "CE", 100, "BUY", 1, entry_price=8.0),
            _leg("B", "CE", 110, "SELL", 1, entry_price=3.0),
            _leg("C", "PE", 90, "SELL", 1, entry_price=2.0, exit_price=1.0, leg_id=3),
        ]
    )

    result = get_basket_payoff(basket)

    # Open bull call spread alone: max_loss -5, max_profit 5.
    # Closed put leg realized: sold 2, bought back 1 -> +1 per unit = +1.
    assert result.max_loss == pytest.approx(-5 + 1)
    assert result.max_profit == pytest.approx(5 + 1)


def test_basket_payoff_when_fully_closed_has_no_open_legs() -> None:
    basket = _basket(
        [
            _leg("A", "CE", 100, "SELL", 1, entry_price=5.0, exit_price=2.0),
        ]
    )

    result = get_basket_payoff(basket)

    assert result.max_profit == pytest.approx(3.0)
    assert result.max_loss == pytest.approx(3.0)
    assert result.breakevens == []
    assert math.isfinite(result.max_profit)

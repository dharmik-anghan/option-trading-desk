from __future__ import annotations

import pytest

from analytics.payoff import Leg
from broker.fake import FakeBroker
from execution.manager import ExecutionManager


def test_build_orders_maps_legs_to_order_requests() -> None:
    manager = ExecutionManager(FakeBroker())
    legs = [
        Leg(option_type="CE", strike=100, premium=5, quantity=2, side="SELL", symbol="X-100-CE"),
        Leg(option_type="PE", strike=90, premium=4, quantity=2, side="BUY", symbol="X-90-PE"),
    ]

    orders = manager.build_orders(legs)

    assert len(orders) == 2
    assert orders[0].symbol == "X-100-CE"
    assert orders[0].side == "SELL"
    assert orders[0].quantity == 2
    assert orders[1].symbol == "X-90-PE"
    assert orders[1].side == "BUY"


def test_build_orders_applies_quantity_multiplier() -> None:
    manager = ExecutionManager(FakeBroker())
    legs = [
        Leg(option_type="CE", strike=100, premium=5, quantity=2, side="SELL", symbol="X-100-CE")
    ]

    orders = manager.build_orders(legs, quantity_multiplier=3)

    assert orders[0].quantity == 6


def test_build_orders_rejects_leg_without_symbol() -> None:
    manager = ExecutionManager(FakeBroker())
    legs = [Leg(option_type="CE", strike=100, premium=5, quantity=1, side="SELL")]

    with pytest.raises(ValueError, match="symbol"):
        manager.build_orders(legs)


def test_place_all_sends_every_order_to_the_broker() -> None:
    broker = FakeBroker()
    manager = ExecutionManager(broker)
    legs = [
        Leg(option_type="CE", strike=100, premium=5, quantity=1, side="SELL", symbol="X-100-CE"),
        Leg(option_type="PE", strike=90, premium=4, quantity=1, side="BUY", symbol="X-90-PE"),
    ]
    orders = manager.build_orders(legs)

    results = manager.place_all(orders)

    assert len(results) == 2
    assert len(broker.placed_orders) == 2
    assert broker.placed_orders[0].symbol == "X-100-CE"
    assert all(r.order_id for r in results)

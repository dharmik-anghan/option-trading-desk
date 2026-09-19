"""The only code allowed to call `Broker.place_order` — strategies emit
`Leg`s, `ExecutionManager` is the seam that turns them into real orders.
"""

from __future__ import annotations

from analytics.payoff import Leg
from broker.base import Broker
from broker.models import OrderRequest, OrderResult


class ExecutionManager:
    def __init__(self, broker: Broker) -> None:
        self._broker = broker

    def build_orders(self, legs: list[Leg], quantity_multiplier: int = 1) -> list[OrderRequest]:
        orders = []
        for leg in legs:
            if not leg.symbol:
                raise ValueError(f"Leg {leg} has no symbol; can't place a real order for it")
            orders.append(
                OrderRequest(
                    symbol=leg.symbol,
                    quantity=leg.quantity * quantity_multiplier,
                    side=leg.side,
                    order_type="MARKET",
                )
            )
        return orders

    def place_all(self, orders: list[OrderRequest]) -> list[OrderResult]:
        return [self._broker.place_order(order) for order in orders]

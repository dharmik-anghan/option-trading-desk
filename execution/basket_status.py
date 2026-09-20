"""A basket's combined payoff: open legs' theoretical payoff-at-expiry plus
P&L already banked from legs that have since been closed.

This is the point of tracking baskets ourselves (`storage/basket_repo.py`)
instead of inferring "strategy groups" from live broker positions - a
naive recompute over whatever's currently open would silently drop the
outcome of any leg you've already exited (e.g. an early adjustment).
"""

from __future__ import annotations

from analytics.payoff import Leg, PayoffResult, analyze
from storage.basket_repo import Basket, BasketLeg


def leg_realized_pnl(leg: BasketLeg) -> float:
    if leg.exit_price is None:
        return 0.0
    diff = leg.exit_price - leg.entry_price
    sign = 1 if leg.side == "BUY" else -1
    return sign * diff * leg.quantity


def get_basket_payoff(basket: Basket) -> PayoffResult:
    open_legs = [leg for leg in basket.legs if leg.is_open]
    realized_offset = sum(leg_realized_pnl(leg) for leg in basket.legs if not leg.is_open)

    if not open_legs:
        return PayoffResult(
            legs=[],
            max_profit=realized_offset,
            max_loss=realized_offset,
            breakevens=[],
            realized_offset=realized_offset,
        )

    legs = [
        Leg(
            option_type=leg.option_type,
            strike=leg.strike,
            premium=leg.entry_price,
            quantity=leg.quantity,
            side=leg.side,
            symbol=leg.symbol,
        )
        for leg in open_legs
    ]
    return analyze(legs, realized_offset=realized_offset)

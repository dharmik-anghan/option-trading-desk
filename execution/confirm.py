"""The human-in-the-loop gate: never place a real order without an exact
"CONFIRM" response, and never even ask if the pre-trade risk checks failed.

`confirm` is injected (defaults to real `input`) so this is testable without
a terminal, and so a future non-CLI surface (e.g. a dashboard) can supply
its own confirmation mechanism without changing this logic.
"""

from __future__ import annotations

from collections.abc import Callable

from analytics.payoff import Leg, PayoffResult
from broker.models import OrderResult
from execution.manager import ExecutionManager
from risk.pre_trade_check import PreTradeCheckResult

CONFIRM_TOKEN = "CONFIRM"


def confirm_and_place(
    legs: list[Leg],
    payoff: PayoffResult,
    pre_trade: PreTradeCheckResult,
    manager: ExecutionManager,
    quantity_multiplier: int = 1,
    confirm: Callable[[str], str] = input,
) -> list[OrderResult] | None:
    if not pre_trade.passed:
        print("Pre-trade checks failed - refusing to place orders:")
        for check in pre_trade.checks:
            if not check.passed:
                print(f"  [FAIL] {check.reason}")
        return None

    print(f"About to place {len(legs)} real order(s):")
    for leg in legs:
        qty = leg.quantity * quantity_multiplier
        print(f"  {leg.side} {qty}x {leg.option_type} {leg.strike} ({leg.symbol})")
    print(f"Max profit: {payoff.max_profit}  Max loss: {payoff.max_loss}")

    answer = confirm(
        f"Type {CONFIRM_TOKEN} exactly to place these REAL orders, anything else to abort: "
    )
    if answer != CONFIRM_TOKEN:
        print("Not confirmed - aborting, no orders placed.")
        return None

    orders = manager.build_orders(legs, quantity_multiplier=quantity_multiplier)
    return manager.place_all(orders)

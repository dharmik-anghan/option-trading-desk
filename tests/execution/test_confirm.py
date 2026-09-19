"""The confirm-then-place flow is the last line of defense before a real
order goes out, so these tests focus on: it must never place an order
without an exact "CONFIRM" response, and it must never even ask for
confirmation if the pre-trade risk checks already failed.
"""

from __future__ import annotations

from collections.abc import Callable

from analytics.payoff import Leg, analyze
from broker.fake import FakeBroker
from execution.confirm import confirm_and_place
from execution.manager import ExecutionManager
from risk.pre_trade_check import PreTradeCheckResult
from risk.result import RiskCheckResult


def _refuses_to_be_called(_: str) -> str:
    raise AssertionError("confirm() must not be called when pre-trade checks failed")


def _capped_legs() -> list[Leg]:
    return [
        Leg(option_type="PE", strike=90, premium=1, quantity=1, side="BUY", symbol="X-90-PE"),
        Leg(option_type="PE", strike=95, premium=3, quantity=1, side="SELL", symbol="X-95-PE"),
    ]


def test_places_orders_when_confirmed_with_exact_string() -> None:
    broker = FakeBroker()
    manager = ExecutionManager(broker)
    legs = _capped_legs()
    payoff = analyze(legs)
    pre_trade = PreTradeCheckResult(
        checks=[RiskCheckResult(passed=True, reason="ok")], max_quantity=5
    )

    results = confirm_and_place(legs, payoff, pre_trade, manager, confirm=lambda _: "CONFIRM")

    assert results is not None
    assert len(results) == 2
    assert len(broker.placed_orders) == 2


def test_does_not_place_orders_on_any_other_response() -> None:
    broker = FakeBroker()
    manager = ExecutionManager(broker)
    legs = _capped_legs()
    payoff = analyze(legs)
    pre_trade = PreTradeCheckResult(
        checks=[RiskCheckResult(passed=True, reason="ok")], max_quantity=5
    )

    def make_confirm(response: str) -> Callable[[str], str]:
        return lambda _: response

    for bad_response in ["confirm", "yes", "CONFIRM ", "", "y"]:
        results = confirm_and_place(
            legs, payoff, pre_trade, manager, confirm=make_confirm(bad_response)
        )
        assert results is None

    assert len(broker.placed_orders) == 0


def test_does_not_ask_for_confirmation_when_pre_trade_checks_failed() -> None:
    broker = FakeBroker()
    manager = ExecutionManager(broker)
    legs = _capped_legs()
    payoff = analyze(legs)
    pre_trade = PreTradeCheckResult(
        checks=[RiskCheckResult(passed=False, reason="insufficient margin")], max_quantity=0
    )

    results = confirm_and_place(legs, payoff, pre_trade, manager, confirm=_refuses_to_be_called)

    assert results is None
    assert len(broker.placed_orders) == 0

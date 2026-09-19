from __future__ import annotations

from analytics.payoff import Leg, PayoffResult, analyze
from risk.pre_trade_check import run_pre_trade_checks


def _capped_strategy_payoff() -> PayoffResult:
    # Bull put credit spread: sell 95 put @ 3, buy 90 put @ 1. Max loss = 5-2 = -3.
    legs = [
        Leg(option_type="PE", strike=90, premium=1, quantity=1, side="BUY"),
        Leg(option_type="PE", strike=95, premium=3, quantity=1, side="SELL"),
    ]
    return analyze(legs)


def _naked_strategy_payoff() -> PayoffResult:
    # A naked short call has truly unbounded risk (no upper bound on the
    # underlying) - unlike a naked short put, whose max loss is large but
    # finite (bounded at the S=0 floor).
    legs = [Leg(option_type="CE", strike=105, premium=3, quantity=1, side="SELL")]
    return analyze(legs)


def test_all_checks_pass_for_a_well_capitalized_capped_trade() -> None:
    result = run_pre_trade_checks(
        payoff=_capped_strategy_payoff(),
        available_funds=50000,
        required_margin=10000,
        capital=100000,
        max_risk_pct=2.0,
        max_loss_limit=1000,
    )

    assert result.passed is True
    assert result.max_quantity > 0


def test_fails_when_margin_insufficient() -> None:
    result = run_pre_trade_checks(
        payoff=_capped_strategy_payoff(),
        available_funds=100,
        required_margin=10000,
        capital=100000,
        max_risk_pct=2.0,
        max_loss_limit=1000,
    )

    assert result.passed is False


def test_unbounded_risk_trade_fails_and_has_zero_sizeable_quantity() -> None:
    result = run_pre_trade_checks(
        payoff=_naked_strategy_payoff(),
        available_funds=50000,
        required_margin=10000,
        capital=100000,
        max_risk_pct=2.0,
        max_loss_limit=1000,
    )

    assert result.passed is False
    assert result.max_quantity == 0

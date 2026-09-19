"""Payoff/max-profit/max-loss/breakeven tests against hand-computed values
for textbook option strategies.
"""

from __future__ import annotations

import math

import pytest

from analytics.payoff import Leg, analyze


def test_long_call_has_unbounded_profit_and_capped_loss() -> None:
    # Buy 1 lot 100-strike call for premium 5.
    legs = [Leg(option_type="CE", strike=100, premium=5, quantity=1, side="BUY")]

    result = analyze(legs)

    assert result.max_loss == pytest.approx(-5)
    assert result.max_profit == math.inf
    assert result.breakevens == pytest.approx([105])


def test_short_put_has_capped_profit_and_large_capped_loss() -> None:
    # Sell 1 lot 100-strike put for premium 5. Max loss is at S=0: premium - strike.
    legs = [Leg(option_type="PE", strike=100, premium=5, quantity=1, side="SELL")]

    result = analyze(legs)

    assert result.max_profit == pytest.approx(5)
    assert result.max_loss == pytest.approx(5 - 100)
    assert result.breakevens == pytest.approx([95])


def test_bull_call_spread_is_fully_capped() -> None:
    # Buy 100-call @ 8, sell 110-call @ 3. Net debit 5. Max profit = 10 - 5 = 5.
    legs = [
        Leg(option_type="CE", strike=100, premium=8, quantity=1, side="BUY"),
        Leg(option_type="CE", strike=110, premium=3, quantity=1, side="SELL"),
    ]

    result = analyze(legs)

    assert result.max_loss == pytest.approx(-5)
    assert result.max_profit == pytest.approx(5)
    assert result.breakevens == pytest.approx([105])


def test_long_straddle_unbounded_profit_both_sides() -> None:
    # Buy 100-call @ 4 and 100-put @ 4. Total premium 8.
    legs = [
        Leg(option_type="CE", strike=100, premium=4, quantity=1, side="BUY"),
        Leg(option_type="PE", strike=100, premium=4, quantity=1, side="BUY"),
    ]

    result = analyze(legs)

    assert result.max_loss == pytest.approx(-8)
    assert result.max_profit == math.inf
    assert result.breakevens == pytest.approx([92, 108])


def test_iron_condor_fully_capped_both_directions() -> None:
    # Classic iron condor: sell 95-put(3)/buy 90-put(1), sell 105-call(3)/buy 110-call(1).
    # Net credit = 3 - 1 + 3 - 1 = 4. Wing width = 5. Max loss = 5 - 4 = 1.
    legs = [
        Leg(option_type="PE", strike=90, premium=1, quantity=1, side="BUY"),
        Leg(option_type="PE", strike=95, premium=3, quantity=1, side="SELL"),
        Leg(option_type="CE", strike=105, premium=3, quantity=1, side="SELL"),
        Leg(option_type="CE", strike=110, premium=1, quantity=1, side="BUY"),
    ]

    result = analyze(legs)

    assert result.max_profit == pytest.approx(4)
    assert result.max_loss == pytest.approx(-1)
    assert result.breakevens == pytest.approx([91, 109])


def test_quantity_scales_payoff() -> None:
    legs = [Leg(option_type="CE", strike=100, premium=5, quantity=3, side="BUY")]

    result = analyze(legs)

    assert result.max_loss == pytest.approx(-15)
    assert result.payoff_at(120) == pytest.approx((20 - 5) * 3)

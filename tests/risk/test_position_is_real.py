from __future__ import annotations

from analytics.payoff import Leg, PayoffResult, analyze
from risk.limits import check_position_is_real


def _offsetting() -> PayoffResult:
    """What a too-narrow strike window produced on a monthly expiry: every
    leg clamped onto the outermost strike, so all four cancel."""
    legs = [
        Leg(option_type="CE", strike=23900, premium=70.1, quantity=1, side="SELL"),
        Leg(option_type="CE", strike=23900, premium=70.1, quantity=1, side="BUY"),
        Leg(option_type="PE", strike=22400, premium=80.8, quantity=1, side="SELL"),
        Leg(option_type="PE", strike=22400, premium=80.8, quantity=1, side="BUY"),
    ]
    return analyze(legs)


def test_offsetting_legs_are_rejected() -> None:
    result = check_position_is_real(_offsetting())

    assert result.passed is False
    assert "cancel out" in result.reason
    # both cancelling pairs are named, lowest strike first
    assert "22400 PE" in result.reason
    assert "23900 CE" in result.reason


def test_a_genuine_iron_condor_passes() -> None:
    legs = [
        Leg(option_type="CE", strike=23800, premium=80.0, quantity=1, side="SELL"),
        Leg(option_type="CE", strike=24200, premium=40.0, quantity=1, side="BUY"),
        Leg(option_type="PE", strike=22500, premium=75.0, quantity=1, side="SELL"),
        Leg(option_type="PE", strike=22100, premium=35.0, quantity=1, side="BUY"),
    ]

    assert check_position_is_real(analyze(legs)).passed is True


def test_a_ratio_spread_at_one_strike_is_not_offsetting() -> None:
    # same strike both ways but unequal size, so real exposure remains
    legs = [
        Leg(option_type="CE", strike=23800, premium=80.0, quantity=2, side="SELL"),
        Leg(option_type="CE", strike=23800, premium=80.0, quantity=1, side="BUY"),
        Leg(option_type="CE", strike=24200, premium=40.0, quantity=1, side="BUY"),
    ]

    assert check_position_is_real(analyze(legs)).passed is True


def test_a_position_that_can_neither_gain_nor_lose_is_rejected() -> None:
    # no duplicate strikes to point at, but still nothing at stake
    flat = PayoffResult(legs=[], max_profit=0.0, max_loss=0.0, breakevens=[])

    result = check_position_is_real(flat)

    assert result.passed is False
    assert "no position" in result.reason

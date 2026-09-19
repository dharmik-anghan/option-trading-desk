from __future__ import annotations

import pytest

from analytics.payoff import Leg
from broker.models import Greeks
from risk.portfolio_greeks import LegGreeks, aggregate_portfolio_greeks


def test_bought_leg_contributes_its_own_sign() -> None:
    leg = Leg(option_type="CE", strike=100, premium=5, quantity=1, side="BUY")
    greeks = Greeks(delta=0.5, gamma=0.02, theta=-1.0, vega=2.0, iv=15.0)

    result = aggregate_portfolio_greeks([LegGreeks(leg=leg, greeks=greeks)])

    assert result.delta == pytest.approx(0.5)
    assert result.gamma == pytest.approx(0.02)
    assert result.theta == pytest.approx(-1.0)
    assert result.vega == pytest.approx(2.0)


def test_sold_leg_flips_sign_relative_to_being_long() -> None:
    leg = Leg(option_type="CE", strike=100, premium=5, quantity=1, side="SELL")
    greeks = Greeks(delta=0.5, gamma=0.02, theta=-1.0, vega=2.0, iv=15.0)

    result = aggregate_portfolio_greeks([LegGreeks(leg=leg, greeks=greeks)])

    assert result.delta == pytest.approx(-0.5)
    assert result.gamma == pytest.approx(-0.02)
    assert result.theta == pytest.approx(1.0)  # short options decay in your favor
    assert result.vega == pytest.approx(-2.0)


def test_quantity_scales_contribution() -> None:
    leg = Leg(option_type="CE", strike=100, premium=5, quantity=3, side="BUY")
    greeks = Greeks(delta=0.5, gamma=0.0, theta=0.0, vega=0.0, iv=15.0)

    result = aggregate_portfolio_greeks([LegGreeks(leg=leg, greeks=greeks)])

    assert result.delta == pytest.approx(1.5)


def test_short_strangle_deltas_partially_offset() -> None:
    # Sell a call (delta 0.30) and sell a put (delta -0.30): both sold, so
    # portfolio delta contributions are -0.30 and +0.30 -> net ~0 (delta-neutral).
    call_leg = Leg(option_type="CE", strike=110, premium=10, quantity=1, side="SELL")
    put_leg = Leg(option_type="PE", strike=90, premium=10, quantity=1, side="SELL")
    call_greeks = Greeks(delta=0.30, gamma=0.01, theta=-2.0, vega=3.0, iv=15.0)
    put_greeks = Greeks(delta=-0.30, gamma=0.01, theta=-2.0, vega=3.0, iv=15.0)

    result = aggregate_portfolio_greeks(
        [LegGreeks(leg=call_leg, greeks=call_greeks), LegGreeks(leg=put_leg, greeks=put_greeks)]
    )

    assert result.delta == pytest.approx(0.0)
    assert result.theta == pytest.approx(4.0)  # both legs sold -> both decay in our favor

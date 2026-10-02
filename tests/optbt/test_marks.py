"""Carrying an option's last price to the market now."""

from __future__ import annotations

import pytest

from analytics import black_scholes as bs
from optbt.marks import implied_vol, reprice

MONTH = 30 / 365


def test_with_the_market_where_it_was_the_price_is_unchanged() -> None:
    assert reprice(
        120.0, 23500, "CE", spot_then=23450, years_then=MONTH, spot_now=23450, years_now=MONTH
    ) == pytest.approx(120.0, abs=0.01)


def test_a_put_carried_into_a_fall_is_worth_what_the_fall_made_it() -> None:
    """At the volatility its old price implied, priced at the index now."""
    sigma = implied_vol(150.0, 23450, 23400, MONTH, "PE")
    assert sigma is not None
    now = reprice(
        150.0, 23400, "PE", spot_then=23450, years_then=MONTH, spot_now=22750, years_now=MONTH
    )
    assert now == pytest.approx(bs.price(22750, 23400, 0.0, sigma, MONTH, "PE"))
    assert now > 650  # at least what exercising it pays


def test_an_option_with_no_time_value_moves_with_the_index() -> None:
    """Deep in the money and trading at intrinsic, there is no volatility to read;
    each point the index falls is a point on the put."""
    now = reprice(
        700.0, 23450, "PE", spot_then=22750, years_then=MONTH, spot_now=22250, years_now=MONTH
    )
    assert now == pytest.approx(1200.0)


def test_at_expiry_an_option_is_worth_its_intrinsic_value() -> None:
    assert reprice(
        40.0, 23400, "CE", spot_then=23380, years_then=MONTH, spot_now=23460, years_now=0.0
    ) == pytest.approx(60.0)

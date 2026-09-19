"""Black-Scholes tests against a known textbook reference (Hull, "Options,
Futures, and Other Derivatives" — S=42, K=40, r=10%, sigma=20%, T=0.5y),
not against our own implementation re-derived a second way.
"""

from __future__ import annotations

import pytest

from analytics.black_scholes import greeks, price

S, K, R, SIGMA, T = 42.0, 40.0, 0.10, 0.20, 0.5


def test_call_price_matches_reference() -> None:
    assert price(S, K, R, SIGMA, T, "CE") == pytest.approx(4.76, abs=0.01)


def test_put_price_matches_reference() -> None:
    assert price(S, K, R, SIGMA, T, "PE") == pytest.approx(0.81, abs=0.01)


def test_call_greeks_match_reference() -> None:
    g = greeks(S, K, R, SIGMA, T, "CE")
    assert g.delta == pytest.approx(0.7791, abs=0.001)
    assert g.gamma == pytest.approx(0.04996, abs=0.0005)
    assert g.vega == pytest.approx(8.8134, abs=0.001)
    assert g.theta == pytest.approx(-4.5591, abs=0.001)
    assert g.iv == pytest.approx(SIGMA * 100, abs=0.001)


def test_put_greeks_match_reference() -> None:
    g = greeks(S, K, R, SIGMA, T, "PE")
    assert g.delta == pytest.approx(-0.2209, abs=0.001)
    assert g.theta == pytest.approx(-0.7542, abs=0.001)


def test_put_call_parity_holds() -> None:
    call = price(S, K, R, SIGMA, T, "CE")
    put = price(S, K, R, SIGMA, T, "PE")
    # C - P = S - K * e^(-rT)
    import math

    assert call - put == pytest.approx(S - K * math.exp(-R * T), abs=1e-9)

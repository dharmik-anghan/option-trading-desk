from __future__ import annotations

import math

import pytest

from analytics.payoff import DEFAULT_RATE, implied_rate


def _pair_at(
    strike: float, spot: float, rate: float, time_years: float, call: float
) -> tuple[float, float, float]:
    """A call/put pair that satisfies parity exactly at `rate`."""
    put = call - spot + strike * math.exp(-rate * time_years)
    return (strike, call, put)


def test_recovers_the_rate_a_consistent_pair_was_built_with() -> None:
    spot, T, rate = 23140.0, 31.0 / 365, 0.0605
    pairs = [_pair_at(k, spot, rate, T, 500.0) for k in (22900.0, 23000.0, 23100.0)]

    assert implied_rate(pairs, spot, T) == pytest.approx(rate, abs=1e-6)


def test_a_single_stale_quote_cannot_set_the_rate() -> None:
    spot, T, rate = 23140.0, 31.0 / 365, 0.06
    good = [_pair_at(k, spot, rate, T, 500.0) for k in (22900.0, 23000.0, 23100.0, 23200.0)]
    # one strike quoted well away from parity
    stale = (23300.0, 700.0, 100.0)

    assert implied_rate([*good, stale], spot, T) == pytest.approx(rate, abs=1e-3)


def test_near_expiry_noise_is_rejected_rather_than_believed() -> None:
    # the real case: three days to run, and live prices implied 25%
    spot, T = 23140.5, 3.22 / 365
    pairs = [
        (22950.0, 265.90, 24.95),
        (23000.0, 224.35, 34.00),
        (23050.0, 184.55, 44.40),
        (23100.0, 150.15, 58.30),
    ]

    assert implied_rate(pairs, spot, T) == DEFAULT_RATE


def test_real_october_prices_imply_about_six_percent() -> None:
    # taken from the live chain, 31 days to expiry
    spot, T = 23140.5, 31.22 / 365
    pairs = [
        (22900.0, 531.45, 175.40),
        (23000.0, 465.30, 206.15),
        (23100.0, 402.15, 239.50),
        (23200.0, 341.55, 278.40),
        (23300.0, 287.00, 322.50),
    ]

    assert implied_rate(pairs, spot, T) == pytest.approx(0.0605, abs=0.004)


def test_no_usable_pairs_falls_back() -> None:
    assert implied_rate([], 23140.0, 0.1) == DEFAULT_RATE
    assert implied_rate([(23000.0, 0.0, 0.0)], 23140.0, 0.1) == DEFAULT_RATE


def test_zero_time_falls_back() -> None:
    assert implied_rate([(23000.0, 400.0, 200.0)], 23140.0, 0.0) == DEFAULT_RATE


def test_the_band_can_be_tightened() -> None:
    spot, T = 23140.0, 31.0 / 365
    pairs = [_pair_at(23000.0, spot, 0.12, T, 500.0)]

    assert implied_rate(pairs, spot, T) == pytest.approx(0.12, abs=1e-6)
    assert implied_rate(pairs, spot, T, plausible=(0.0, 0.08)) == DEFAULT_RATE

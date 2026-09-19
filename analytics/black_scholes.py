"""Self-contained Black-Scholes pricer and Greeks.

Deliberately dependency-free (plain `math`, no numpy/scipy) — this is a
handful of closed-form formulas, and pulling in scipy for `erf` would
contradict the "lightweight" stack decision (see docs in the project plan).

This exists as a *fallback*: Fyers' option chain already returns Greeks/IV
per strike (see `broker/fyers.py`), so `FyersBroker` doesn't need this. It
becomes load-bearing once a broker that doesn't supply Greeks is added, and
is also the basis for payoff/max-loss/breakeven math in `analytics/payoff.py`
which no broker computes for multi-leg strategies.
"""

from __future__ import annotations

import math

from broker.models import Greeks, OptionType


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    return math.exp(-(x**2) / 2) / math.sqrt(2 * math.pi)


def _d1_d2(
    spot: float, strike: float, rate: float, sigma: float, time_years: float
) -> tuple[float, float]:
    d1 = (math.log(spot / strike) + (rate + sigma**2 / 2) * time_years) / (
        sigma * math.sqrt(time_years)
    )
    d2 = d1 - sigma * math.sqrt(time_years)
    return d1, d2


def price(
    spot: float,
    strike: float,
    rate: float,
    sigma: float,
    time_years: float,
    option_type: OptionType,
) -> float:
    """Black-Scholes price for a European option (no dividend yield)."""
    d1, d2 = _d1_d2(spot, strike, rate, sigma, time_years)
    discounted_strike = strike * math.exp(-rate * time_years)
    if option_type == "CE":
        return spot * _norm_cdf(d1) - discounted_strike * _norm_cdf(d2)
    return discounted_strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def greeks(
    spot: float,
    strike: float,
    rate: float,
    sigma: float,
    time_years: float,
    option_type: OptionType,
) -> Greeks:
    """Delta, Gamma, Theta (per year), Vega (per 1.00 of vol), and the input IV (as %)."""
    d1, d2 = _d1_d2(spot, strike, rate, sigma, time_years)
    pdf_d1 = _norm_pdf(d1)
    sqrt_t = math.sqrt(time_years)
    discounted_strike = strike * math.exp(-rate * time_years)

    gamma = pdf_d1 / (spot * sigma * sqrt_t)
    vega = spot * pdf_d1 * sqrt_t
    decay_term = -(spot * pdf_d1 * sigma) / (2 * sqrt_t)

    if option_type == "CE":
        delta = _norm_cdf(d1)
        theta = decay_term - rate * discounted_strike * _norm_cdf(d2)
    else:
        delta = _norm_cdf(d1) - 1
        theta = decay_term + rate * discounted_strike * _norm_cdf(-d2)

    return Greeks(delta=delta, gamma=gamma, theta=theta, vega=vega, iv=sigma * 100)

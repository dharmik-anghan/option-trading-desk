"""Aggregate per-instrument Greeks across the legs of a strategy/portfolio.

A bought option's position Greeks equal the instrument's own Greeks scaled
by quantity; a sold option's are the negative of that (you're short that
exposure) - e.g. a sold call's positive theta becomes a positive
contribution to portfolio theta (decay works in your favor when short).
"""

from __future__ import annotations

from dataclasses import dataclass

from analytics.payoff import Leg
from broker.models import Greeks


@dataclass(frozen=True)
class LegGreeks:
    leg: Leg
    greeks: Greeks


@dataclass(frozen=True)
class PortfolioGreeks:
    delta: float
    gamma: float
    theta: float
    vega: float


def aggregate_portfolio_greeks(legs: list[LegGreeks]) -> PortfolioGreeks:
    delta = gamma = theta = vega = 0.0
    for entry in legs:
        sign = 1 if entry.leg.side == "BUY" else -1
        weight = sign * entry.leg.quantity
        delta += weight * entry.greeks.delta
        gamma += weight * entry.greeks.gamma
        theta += weight * entry.greeks.theta
        vega += weight * entry.greeks.vega
    return PortfolioGreeks(delta=delta, gamma=gamma, theta=theta, vega=vega)

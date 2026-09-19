"""Multi-leg option payoff, max-profit/max-loss, and breakeven calculator.

No broker computes this for a multi-leg combination — it's derived here from
each leg's intrinsic value at expiry. The payoff of any combination of
options is piecewise linear in the underlying price, with kinks only at the
strikes involved, so extrema and root-crossings (breakevens) only need to be
checked at those kink points plus the S=0 boundary and the asymptotic slope
beyond the largest strike.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from broker.models import OptionType, Side


@dataclass(frozen=True)
class Leg:
    option_type: OptionType
    strike: float
    premium: float
    quantity: int
    side: Side
    # The broker-tradable symbol (e.g. from OptionChainRow.symbol). Optional
    # because payoff math doesn't need it - only execution/ does, to place a
    # real order. Kept optional so pure payoff tests don't need a real chain.
    symbol: str | None = None


@dataclass(frozen=True)
class PayoffResult:
    legs: list[Leg]
    max_profit: float
    max_loss: float
    breakevens: list[float]

    def payoff_at(self, spot: float) -> float:
        return _net_payoff(self.legs, spot)


def _leg_payoff(leg: Leg, spot: float) -> float:
    if leg.option_type == "CE":
        intrinsic = max(spot - leg.strike, 0.0)
    else:
        intrinsic = max(leg.strike - spot, 0.0)
    unit_value = intrinsic - leg.premium if leg.side == "BUY" else leg.premium - intrinsic
    return unit_value * leg.quantity


def _net_payoff(legs: list[Leg], spot: float) -> float:
    return sum(_leg_payoff(leg, spot) for leg in legs)


def _slope_above_max_strike(legs: list[Leg]) -> float:
    """Rate of change of net payoff per unit of underlying, above every strike.

    Only calls contribute beyond the highest strike (puts are worthless
    there); a bought call adds +quantity, a sold call adds -quantity.
    """
    slope = 0.0
    for leg in legs:
        if leg.option_type != "CE":
            continue
        slope += leg.quantity if leg.side == "BUY" else -leg.quantity
    return slope


def _find_breakevens(points: list[float], values: list[float], slope_above: float) -> list[float]:
    roots: set[float] = set()
    for point, value in zip(points, values, strict=True):
        if abs(value) < 1e-9:
            roots.add(round(point, 6))
    for i in range(len(points) - 1):
        v0, v1 = values[i], values[i + 1]
        if v0 * v1 < 0:
            x0, x1 = points[i], points[i + 1]
            root = x0 + (x1 - x0) * (0 - v0) / (v1 - v0)
            roots.add(round(root, 6))
    if abs(slope_above) > 1e-9:
        last_value = values[-1]
        if abs(last_value) >= 1e-9 and (last_value > 0) != (slope_above > 0):
            roots.add(round(points[-1] - last_value / slope_above, 6))
    return sorted(roots)


def analyze(legs: list[Leg]) -> PayoffResult:
    if not legs:
        raise ValueError("At least one leg is required")

    points = sorted({0.0, *(leg.strike for leg in legs)})
    values = [_net_payoff(legs, p) for p in points]
    slope_above = _slope_above_max_strike(legs)

    max_profit = math.inf if slope_above > 1e-9 else max(values)
    max_loss = -math.inf if slope_above < -1e-9 else min(values)
    breakevens = _find_breakevens(points, values, slope_above)

    return PayoffResult(legs=legs, max_profit=max_profit, max_loss=max_loss, breakevens=breakevens)

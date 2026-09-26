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

from analytics import black_scholes
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
    # P&L already realized from legs no longer part of this payoff (e.g. a
    # basket's closed legs) - folded into max_profit/max_loss/breakevens
    # already, kept here only so payoff_at() can apply the same shift.
    realized_offset: float = 0.0

    def payoff_at(self, spot: float) -> float:
        return _net_payoff(self.legs, spot) + self.realized_offset


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


def payoff_curve_points(
    result: PayoffResult, include: float | None = None
) -> list[tuple[float, float]]:
    """Vertices sufficient to draw an exact payoff chart.

    The payoff is piecewise linear with kinks only at strikes, so a chart
    only needs: each strike, each breakeven (so a renderer can split the
    line into never-mixed-sign segments for area shading without
    guessing), and padded domain edges beyond the outermost strike/
    breakeven so the sloped or flat tails are visible.

    `include` is a price the domain must cover - in practice the current spot.
    Without it the domain spans only the strikes, and a spread sitting away
    from the money produces a chart that does not contain the market: a bull
    put spread at 22500/22900 drew 22,440 to 22,960 while NIFTY was at 23,140,
    so the "you are here" line fell off the edge and no amount of zooming
    brought it back.

    Returns `[]` for a `PayoffResult` with no legs (a fully-closed basket
    has nothing left to plot a curve for - just a flat realized total).
    """
    if not result.legs:
        return []

    strikes = [leg.strike for leg in result.legs]
    interesting = [*strikes, *result.breakevens]
    if include is not None and include > 0:
        interesting.append(include)
    lo, hi = min(interesting), max(interesting)
    pad = max((hi - lo) * 0.15, 1.0)
    x_min = max(0.0, lo - pad)
    x_max = hi + pad

    xs = sorted({x_min, x_max, *strikes, *result.breakevens})
    return [(x, result.payoff_at(x)) for x in xs]


def analyze(legs: list[Leg], realized_offset: float = 0.0) -> PayoffResult:
    if not legs:
        raise ValueError("At least one leg is required")

    points = sorted({0.0, *(leg.strike for leg in legs)})
    values = [_net_payoff(legs, p) + realized_offset for p in points]
    slope_above = _slope_above_max_strike(legs)

    max_profit = math.inf if slope_above > 1e-9 else max(values)
    max_loss = -math.inf if slope_above < -1e-9 else min(values)
    breakevens = _find_breakevens(points, values, slope_above)

    return PayoffResult(
        legs=legs,
        max_profit=max_profit,
        max_loss=max_loss,
        breakevens=breakevens,
        realized_offset=realized_offset,
    )


# Indian risk-free proxy. It only affects discounting, not the shape of the
# curve, so a reasonable constant beats threading a rate through every caller.
DEFAULT_RATE = 0.065

#: What counts as a believable annual carry for an Indian index. Outside this,
#: a derived rate is quote noise rather than a signal - a three-day expiry's
#: futures premium divided by a tiny year-fraction reported 24%.
PLAUSIBLE_RATE = (0.0, 0.15)


def theoretical_curve(
    legs: list[Leg],
    spots: list[float],
    sigmas: list[float],
    time_years: float,
    rate: float = DEFAULT_RATE,
    realized_offset: float = 0.0,
    *,
    calibrate_to: list[float] | None = None,
    at_spot: float | None = None,
) -> list[float]:
    """Mark-to-market P&L across spot at some point *before* expiry.

    `analyze`/`payoff_curve_points` give the payoff at expiry, which is
    piecewise linear and exact from the strikes alone. Before expiry the
    position is worth its Black-Scholes value instead, so this curve is
    smooth, and it is what actually tells you where you stand today.

    Near the strikes, where extrinsic value is largest, a short-premium
    position sits well below its expiry payoff - that time value has not been
    earned yet and would have to be bought back today. Deep in the money the
    gap can invert, because a European option discounts to slightly less than
    its intrinsic value. The two curves meet only at expiry.

    `calibrate_to` anchors the curve to reality. Pass each leg's live market
    price and the `at_spot` it was seen at, and the difference between that and
    what this model makes of it is carried as a constant per leg. Without it the
    curve is a pure reconstruction: the broker derives its implied vol with its
    own model and rate, so feeding that vol back through a different pricer
    misses the traded price by a rupee or several per leg - about 175 rupees
    across four legs of 65, which the curve then reported as profit the position
    did not have. Calibrated, the curve passes exactly through the real
    mark-to-market at `at_spot` and the model supplies only the shape either
    side of it, which is what it is actually good for.

    `sigmas` is one implied vol per leg, in the same order as `legs`, as a
    decimal (0.13 for 13%). A leg falls back to intrinsic value - what the
    expiry curve would say, the honest answer when there is nothing to price
    with - whenever its vol is unknown, or the spot or strike is non-positive.
    Black-Scholes needs log(spot/strike), so those inputs have no answer at
    all rather than a bad one.
    """
    if len(sigmas) != len(legs):
        raise ValueError("sigmas must have one entry per leg")
    if calibrate_to is not None and len(calibrate_to) != len(legs):
        raise ValueError("calibrate_to must have one entry per leg")
    if (calibrate_to is None) != (at_spot is None):
        raise ValueError("calibrate_to and at_spot go together")

    offsets = [0.0] * len(legs)
    if calibrate_to is not None and at_spot is not None:
        modelled = _leg_values(legs, sigmas, at_spot, time_years, rate)
        offsets = [market - model for market, model in zip(calibrate_to, modelled, strict=True)]

    out: list[float] = []
    for spot in spots:
        values = _leg_values(legs, sigmas, spot, time_years, rate)
        total = 0.0
        for leg, value, offset in zip(legs, values, offsets, strict=True):
            direction = 1 if leg.side == "BUY" else -1
            total += direction * (value + offset - leg.premium) * leg.quantity
        out.append(total + realized_offset)
    return out


def _leg_values(
    legs: list[Leg], sigmas: list[float], spot: float, time_years: float, rate: float
) -> list[float]:
    """Each leg's modelled value at one spot, falling back to intrinsic."""
    out: list[float] = []
    for leg, sigma in zip(legs, sigmas, strict=True):
        if time_years <= 0 or sigma <= 0 or spot <= 0 or leg.strike <= 0:
            out.append(_intrinsic(leg.option_type, spot, leg.strike))
        else:
            out.append(
                black_scholes.price(
                    spot=spot,
                    strike=leg.strike,
                    rate=rate,
                    sigma=sigma,
                    time_years=time_years,
                    option_type=leg.option_type,
                )
            )
    return out


def _intrinsic(option_type: OptionType, spot: float, strike: float) -> float:
    return max(0.0, spot - strike) if option_type == "CE" else max(0.0, strike - spot)


def curve_domain(
    result: PayoffResult, points: int = 81, include: float | None = None
) -> list[float]:
    """An evenly spaced spot grid spanning the same range the expiry curve uses.

    The expiry curve only needs its kink points; a smooth pre-expiry curve
    needs sampling, and sampling it over the same domain keeps the two
    drawable on one pair of axes. `include` is passed through so both curves
    cover the same range - see `payoff_curve_points`.
    """
    vertices = payoff_curve_points(result, include)
    if len(vertices) < 2 or points < 2:
        return [x for x, _ in vertices]
    lo, hi = vertices[0][0], vertices[-1][0]
    step = (hi - lo) / (points - 1)
    return [lo + step * i for i in range(points)]


def implied_rate(
    pairs: list[tuple[float, float, float]],
    spot: float,
    time_years: float,
    *,
    fallback: float = DEFAULT_RATE,
    plausible: tuple[float, float] = PLAUSIBLE_RATE,
) -> float:
    """The carry the option prices themselves imply, from put-call parity.

    For a European pair at the same strike and expiry,
    ``C - P = S - K*exp(-r*T)``, which rearranges to give ``r`` with no
    iteration and no volatility - it holds whatever the smile looks like.

    `pairs` is (strike, call price, put price). The median across strikes is
    taken rather than one ATM reading, because a single stale quote would
    otherwise set the rate for the whole curve.

    Falls back when the answer is not credible. Very near expiry the formula
    divides a few rupees of quote noise by a tiny `time_years` and reports
    absurd figures - a three-day NIFTY expiry gave 25% on live prices - so
    anything outside `plausible` is discarded rather than used.
    """
    if time_years <= 0:
        return fallback

    rates: list[float] = []
    for strike, call, put in pairs:
        if strike <= 0 or call <= 0 or put <= 0:
            continue
        forward_ratio = (spot - call + put) / strike
        if forward_ratio <= 0:
            continue
        rate = -math.log(forward_ratio) / time_years
        if plausible[0] <= rate <= plausible[1]:
            rates.append(rate)

    if not rates:
        return fallback
    rates.sort()
    middle = len(rates) // 2
    if len(rates) % 2:
        return rates[middle]
    return (rates[middle - 1] + rates[middle]) / 2

"""What an option that has not traded lately is worth now.

A contract's last trade can be minutes or a day old, and the market does not
wait for it. On 13 Mar 2026 a condor's short 25150 put, deep in the money in a
falling market, last traded well below its worth; valued at that price the
position showed a profit, its target fired, and the put was bought back at
1,725. On 3 Feb a long put was valued at the previous close after a 700-point
gap. So a stale price is carried forward the way a trader would: the implied
volatility it was trading at, then, re-priced at the index level and time to
expiry now.

Black-76 on the index with no rate: the model is only used for the change since
the last trade, at the volatility that trade implies, so the convention cancels.
"""

from __future__ import annotations

from datetime import date, datetime

from analytics import black_scholes as bs
from broker.models import OptionType
from venues.calendar import NSE_CLOSE

YEAR_SECONDS = 365 * 24 * 3600


def years_to(expiry: date, at: datetime) -> float:
    """Years from `at` to the expiry's settlement at the close."""
    return (datetime.combine(expiry, NSE_CLOSE) - at).total_seconds() / YEAR_SECONDS


def intrinsic(spot: float, strike: float, option_type: OptionType) -> float:
    return max(0.0, spot - strike) if option_type == "CE" else max(0.0, strike - spot)


def implied_vol(
    premium: float, forward: float, strike: float, years: float, option_type: OptionType
) -> float | None:
    """The volatility at which an option is worth `premium`, by bisection.

    None when there is no time value to explain - a premium at or below its
    intrinsic value - or when even an absurd volatility cannot reach it.
    """
    if years <= 0 or premium <= intrinsic(forward, strike, option_type) + 0.01:
        return None
    lo, hi = 0.001, 5.0
    if bs.price(forward, strike, 0.0, hi, years, option_type) < premium:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs.price(forward, strike, 0.0, mid, years, option_type) > premium:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def reprice(
    price: float,
    strike: float,
    option_type: OptionType,
    *,
    spot_then: float,
    years_then: float,
    spot_now: float,
    years_now: float,
) -> float:
    """`price`, traded when the index stood at `spot_then`, carried to now.

    At its own implied volatility when it has time value. When it has none - a
    deep in-the-money option trading at intrinsic - it moves with the index one
    for one, and never below what exercising it would pay.
    """
    now_intrinsic = intrinsic(spot_now, strike, option_type)
    if years_now <= 0:
        return now_intrinsic
    sigma = implied_vol(price, spot_then, strike, years_then, option_type)
    if sigma is None:
        then_intrinsic = intrinsic(spot_then, strike, option_type)
        return max(now_intrinsic, now_intrinsic + (price - then_intrinsic))
    return max(now_intrinsic, bs.price(spot_now, strike, 0.0, sigma, years_now, option_type))

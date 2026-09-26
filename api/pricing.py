"""Pricing an option structure from a chain.

Shared by every router that needs a mark or a payoff curve: the strategy review
endpoint and the basket endpoints both price legs, and duplicating the rate
derivation or the sigma lookup between them is how the two quietly come to
disagree about what the same position is worth.

Reads a chain, returns numbers. No HTTP, no database, and a broker only where
an expiry list has to be fetched.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from analytics.payoff import (
    Leg,
    PayoffResult,
    curve_domain,
    implied_rate,
    theoretical_curve,
)
from api.schemas import (
    PayoffPoint,
)
from broker.base import OptionsBroker
from broker.models import Expiry, OptionChain
from broker.symbols import common_expiry
from storage.basket_repo import Basket

_SECONDS_PER_YEAR = 365.0 * 24 * 3600

def years_to_expiry(chain: OptionChain) -> float | None:
    """Time left on the chain's selected expiry, in years.

    The broker's expiry token is a unix timestamp, which is exactly what this
    needs - no parsing a date out of a contract symbol and guessing a session
    close. Returns None when there is no token to read.
    """
    token = chain.expiry_token
    if not token:
        return None
    try:
        expires_at = datetime.fromtimestamp(int(token), tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None
    seconds = (expires_at - datetime.now(UTC)).total_seconds()
    return max(0.0, seconds) / _SECONDS_PER_YEAR

def leg_marks(legs: list[Leg], chain: OptionChain) -> list[float] | None:
    """Each leg's live traded price, or None if the chain is missing any of them.

    Used to anchor the pre-expiry curve to what the position is actually worth.
    All or nothing: calibrating some legs and not others would tilt the curve
    rather than level it.
    """
    by_symbol = {row.symbol: row for row in chain.rows}
    marks: list[float] = []
    for leg in legs:
        row = by_symbol.get(leg.symbol or "")
        if row is None or row.ltp <= 0:
            return None
        marks.append(row.ltp)
    return marks

def leg_sigmas(legs: list[Leg], chain: OptionChain) -> list[float]:
    """Each leg's implied vol as a decimal, matched by contract symbol.

    Fyers reports IV as a percentage and sends 0 for strikes it has no quote
    for; 0 flows through to `theoretical_curve` as "price this one at
    intrinsic" rather than being mistaken for a zero-vol market.
    """
    by_symbol = {row.symbol: row for row in chain.rows}
    sigmas: list[float] = []
    for leg in legs:
        row = by_symbol.get(leg.symbol or "")
        iv = row.greeks.iv if row is not None and row.greeks is not None else 0.0
        sigmas.append(max(0.0, iv) / 100.0)
    return sigmas

def chain_rate(chain: OptionChain, years: float) -> float:
    """The carry the chain's own call/put pairs imply, by put-call parity.

    Better than a constant: it is what these prices are actually discounting
    at, so the curve stays consistent with the quotes it was built from. A
    hardcoded 6.5% was roughly 0.5 points off what this chain implies, and the
    curve is sensitive enough to the rate that that shows up as hundreds of
    rupees out in the wings. `implied_rate` falls back on its own when the
    answer is not credible, which it is not within a few days of expiry.
    """
    pairs: dict[float, dict[str, float]] = {}
    for row in chain.rows:
        if row.ltp > 0:
            pairs.setdefault(row.strike, {})[row.option_type] = row.ltp
    both = [
        (strike, sides["CE"], sides["PE"])
        for strike, sides in pairs.items()
        if "CE" in sides and "PE" in sides
    ]
    return implied_rate(both, chain.underlying_ltp, years)

def today_curve(result: PayoffResult, chain: OptionChain) -> list[PayoffPoint]:
    """The mark-to-market curve, or nothing if we cannot price it honestly."""
    years = years_to_expiry(chain)
    if years is None or years <= 0:
        return []
    sigmas = leg_sigmas(result.legs, chain)
    if not any(sigma > 0 for sigma in sigmas):
        return []
    spots = curve_domain(result, include=chain.underlying_ltp)
    marks = leg_marks(result.legs, chain)
    values = theoretical_curve(
        result.legs,
        spots,
        sigmas=sigmas,
        time_years=years,
        rate=chain_rate(chain, years),
        realized_offset=result.realized_offset,
        calibrate_to=marks,
        at_spot=chain.underlying_ltp if marks else None,
    )
    return [PayoffPoint(spot=s, payoff=v) for s, v in zip(spots, values, strict=True)]

def basket_live_curve(
    basket: Basket,
    result: PayoffResult,
    broker: OptionsBroker,
    chains: dict[tuple[str, str], OptionChain],
) -> tuple[list[PayoffPoint], float | None, str | None]:
    """A mark-to-market curve for a basket, if one can be priced honestly.

    A basket records its contracts but not its expiry, so the expiry is
    recovered from the leg symbols against the expiries the broker lists
    (see `broker.symbols`). Returns empties when that is not possible: no
    open legs, legs spanning different expiries (a calendar spread has no
    single time-to-expiry), or a feed with no implied vol for those strikes.

    `chains` is a cache keyed by (underlying, expiry token) so several
    baskets on the same expiry cost one chain request between them.
    """
    if not result.legs:
        return [], None, None

    symbols = [leg.symbol for leg in result.legs if leg.symbol]
    if len(symbols) != len(result.legs):
        return [], None, None

    listed = listed_expiries(basket.underlying_symbol, broker, chains)
    expiry = common_expiry(symbols, listed)
    if expiry is None:
        return [], None, None

    key = (basket.underlying_symbol, expiry.token)
    chain = chains.get(key)
    if chain is None:
        chain = broker.get_option_chain(
            basket.underlying_symbol, strike_count=40, expiry_token=expiry.token
        )
        chains[key] = chain

    years = years_to_expiry(chain)
    if years is None or years <= 0:
        return [], None, expiry.date
    sigmas = leg_sigmas(result.legs, chain)
    if not any(sigma > 0 for sigma in sigmas):
        return [], round(years * 365.0, 2), expiry.date

    spots = curve_domain(result, include=chain.underlying_ltp)
    marks = leg_marks(result.legs, chain)
    values = theoretical_curve(
        result.legs,
        spots,
        sigmas=sigmas,
        time_years=years,
        rate=chain_rate(chain, years),
        realized_offset=result.realized_offset,
        calibrate_to=marks,
        at_spot=chain.underlying_ltp if marks else None,
    )
    curve = [PayoffPoint(spot=s, payoff=v) for s, v in zip(spots, values, strict=True)]
    return curve, round(years * 365.0, 2), expiry.date

_EXPIRY_CACHE: dict[str, tuple[float, list[Expiry]]] = {}

_EXPIRY_TTL = 600.0  # the listed contracts change once a day at most

def listed_expiries(
    underlying: str, broker: OptionsBroker, chains: dict[tuple[str, str], OptionChain]
) -> list[Expiry]:
    """Every expiry the broker lists for `underlying`.

    Held for ten minutes: reading it needs a whole chain request, and the
    answer is a contract list that changes daily, not by the second. Before
    this it cost one request per basket per poll purely to re-learn dates we
    already knew.
    """
    for (sym, _token), chain in chains.items():
        if sym == underlying and chain.expiries:
            return chain.expiries

    hit = _EXPIRY_CACHE.get(underlying)
    if hit is not None and time.monotonic() - hit[0] < _EXPIRY_TTL:
        cached: list[Expiry] = hit[1]
        return cached

    chain = broker.get_option_chain(underlying, strike_count=1)
    chains[(underlying, chain.expiry_token or "")] = chain
    if chain.expiries:
        _EXPIRY_CACHE[underlying] = (time.monotonic(), chain.expiries)
    return chain.expiries

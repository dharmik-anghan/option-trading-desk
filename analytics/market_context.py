"""What the chain is saying about the market, as a handful of figures.

Derived here rather than in the browser for three reasons: historical
volatility needs candle history the browser never sees, the payload is a few
numbers instead of a whole chain, and the arithmetic - max pain especially -
is worth having tests around.

Every function takes plain data and returns plain numbers, so none of it needs
a broker to exercise.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from broker.models import OptionChain, OptionChainRow


@dataclass(frozen=True)
class Strike:
    """One strike's call and put, as far as the chain gave us either."""

    strike: float
    call: OptionChainRow | None
    put: OptionChainRow | None


def by_strike(chain: OptionChain) -> list[Strike]:
    """The chain collapsed to one entry per strike, in price order."""
    sides: dict[float, dict[str, OptionChainRow]] = {}
    for row in chain.rows:
        sides.setdefault(row.strike, {})[row.option_type] = row
    return [
        Strike(strike=k, call=v.get("CE"), put=v.get("PE")) for k, v in sorted(sides.items())
    ]


def quoted_iv(row: OptionChainRow | None) -> float | None:
    """A usable implied vol, or None.

    Illiquid wings come back with `iv: 0`, which is the absence of a quote
    rather than a zero-volatility market. Treating it as a number is how a
    skew reading of exactly 0.0 gets reported as "no skew" when it means
    "no data".
    """
    if row is None or row.greeks is None:
        return None
    return row.greeks.iv if row.greeks.iv > 0 else None


def atm_strike(strikes: list[Strike], spot: float) -> float | None:
    """The listed strike nearest to spot."""
    if not strikes:
        return None
    return min(strikes, key=lambda s: abs(s.strike - spot)).strike


def atm_straddle(strikes: list[Strike], spot: float) -> float | None:
    """Call plus put at the money - what the market charges for the move."""
    at = atm_strike(strikes, spot)
    for s in strikes:
        if s.strike == at and s.call is not None and s.put is not None:
            if s.call.ltp > 0 and s.put.ltp > 0:
                return s.call.ltp + s.put.ltp
    return None


def atm_iv(strikes: list[Strike], spot: float) -> float | None:
    """Implied vol at the money, averaged over whichever side is quoted.

    The broker publishes one implied vol per strike, so the two sides agree
    and the average is that single figure.
    """
    at = atm_strike(strikes, spot)
    for s in strikes:
        if s.strike != at:
            continue
        ivs = [iv for iv in (quoted_iv(s.call), quoted_iv(s.put)) if iv is not None]
        return sum(ivs) / len(ivs) if ivs else None
    return None


def put_call_ratio(chain: OptionChain, strikes: list[Strike]) -> float | None:
    """Put open interest over call open interest.

    Prefers the broker's whole-chain totals: summing the loaded strikes gives
    the ratio for a window, not for the contract.
    """
    if chain.call_oi > 0:
        return chain.put_oi / chain.call_oi
    calls = sum(s.call.oi for s in strikes if s.call)
    puts = sum(s.put.oi for s in strikes if s.put)
    return puts / calls if calls > 0 else None


#: Roundness steps a strike might land on, coarsest first.
_ROUND_STEPS = (1000, 500, 250, 100, 50, 25)

#: Strikes a roundness class needs before its median is trusted on its own.
_MIN_CLASS = 3


def strike_step(strikes: list[Strike]) -> float:
    """The gap between adjacent listed strikes, as the exchange sets it."""
    if len(strikes) < 2:
        return 0.0
    return min(b.strike - a.strike for a, b in zip(strikes, strikes[1:], strict=False))


def roundness(strike: float, step: float) -> int:
    """The coarsest round number this strike sits on.

    Traders gravitate to round numbers, so a strike divisible by 1000 carries
    far more open interest than its neighbours whatever the market is doing.
    Grouping by this is what lets that preference be divided out.
    """
    value = int(strike)
    for candidate in _ROUND_STEPS:
        if candidate >= step and value % candidate == 0:
            return candidate
    return max(1, int(step))


@dataclass(frozen=True)
class Wall:
    """A strike where open interest is concentrated."""

    strike: float
    oi: int
    #: Open interest relative to the median for strikes of the same roundness.
    #: Above 1 means unusually heavy for its kind; 1 means merely typical.
    prominence: float
    #: The strike with the plain highest open interest on this side, for
    #: comparison. Usually a round number, and often not the same answer.
    heaviest: float


def oi_wall(strikes: list[Strike], side: str, spot: float) -> Wall | None:
    """Where open interest concentrates on one side of spot.

    Two corrections over taking the plain maximum.

    Only strikes on the side that makes sense are considered: puts below spot
    are support, calls above it are resistance. Unconstrained, the maximum can
    land the wrong side of the money entirely - on a live BANKNIFTY chain the
    heaviest put sat nearly 2,000 points *above* spot, which is not a support
    level by any reading.

    And open interest is judged against the median for strikes of the same
    roundness rather than in absolute terms. On a live NIFTY chain the median
    open interest at multiples of 1000 was ten times that at multiples of 50,
    so the plain maximum is essentially a test of which strike is roundest and
    returns the same handful of numbers whatever the market is doing. Dividing
    that out asks the more useful question: which strike is heavy *for its
    kind*, meaning positioning has gathered there rather than habit.
    """
    wanted_side = "PE" if side == "PE" else "CE"
    live: list[tuple[float, OptionChainRow]] = []
    for entry in strikes:
        # puts below spot are support, calls above it are resistance
        if (entry.strike < spot) != (wanted_side == "PE"):
            continue
        row = entry.put if wanted_side == "PE" else entry.call
        if row is not None and row.oi > 0:
            live.append((entry.strike, row))
    if not live:
        return None

    step = strike_step(strikes)
    groups: dict[int, list[int]] = {}
    for k, row in live:
        groups.setdefault(roundness(k, step), []).append(row.oi)
    # A class needs a few members before its median means anything. With one
    # member the median *is* that strike's own open interest, every prominence
    # comes out at exactly 1.0, and the comparison degenerates into whichever
    # strike happens to be examined first. Thin classes fall back to the median
    # across this whole side, which is still better than raw open interest.
    side_median = statistics.median([row.oi for _k, row in live])
    medians = {
        group: statistics.median(values) if len(values) >= _MIN_CLASS else side_median
        for group, values in groups.items()
    }

    heaviest = max(live, key=lambda pair: pair[1].oi)[0]

    def prominence(strike: float, oi: int) -> float:
        median = medians[roundness(strike, step)]
        return oi / median if median > 0 else 0.0

    best_strike, best_row = max(live, key=lambda pair: prominence(pair[0], pair[1].oi))
    return Wall(
        strike=best_strike,
        oi=best_row.oi,
        prominence=prominence(best_strike, best_row.oi),
        heaviest=heaviest,
    )


def max_pain(strikes: list[Strike]) -> float | None:
    """The strike where the most open interest expires worthless.

    For each candidate, the total intrinsic value every other strike's open
    interest would be owed, minimised. Only meaningful over the strikes
    actually loaded, so a narrow window gives a narrow answer.
    """
    usable = [s for s in strikes if s.call or s.put]
    if len(usable) < 2:
        return None
    # No open interest anywhere means no answer. Pain of exactly zero is a
    # perfectly good one - it is what happens whenever the minimising strike
    # sits between the put pile and the call pile, which is the common case.
    if not any((s.call.oi if s.call else 0) + (s.put.oi if s.put else 0) for s in usable):
        return None

    best: float | None = None
    least = math.inf
    for candidate in usable:
        pain = 0.0
        for s in usable:
            if s.call:
                pain += s.call.oi * max(0.0, candidate.strike - s.strike)
            if s.put:
                pain += s.put.oi * max(0.0, s.strike - candidate.strike)
        if pain < least:
            least = pain
            best = candidate.strike
    return best


def skew(strikes: list[Strike], spot: float, distance: float = 0.03) -> float | None:
    """Downside implied vol minus upside, in vol points.

    Read across strikes rather than between a call and a put at one strike:
    the broker quotes a single vol per strike, so the two sides can never
    disagree there. Strikes with no quote are stepped over rather than read
    as zero.
    """
    down = _nearest_quoted(strikes, spot * (1 - distance), "PE")
    up = _nearest_quoted(strikes, spot * (1 + distance), "CE")
    if down is None or up is None:
        return None
    return down - up


def _nearest_quoted(strikes: list[Strike], target: float, side: str) -> float | None:
    for s in sorted(strikes, key=lambda x: abs(x.strike - target)):
        iv = quoted_iv(s.call if side == "CE" else s.put)
        if iv is not None:
            return iv
    return None


def historical_vol(closes: list[float], sessions: int = 20) -> float | None:
    """Annualised volatility of daily closes, as a percentage.

    The standard deviation of log returns over the last `sessions`, scaled by
    root-252. Compared against implied vol it answers whether options are
    charging more than the index has actually been moving.
    """
    if len(closes) < 3:
        return None
    returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i] > 0 and closes[i - 1] > 0
    ]
    window = returns[-sessions:]
    if len(window) < 3:
        return None
    return statistics.stdev(window) * math.sqrt(252) * 100


def futures_symbol(option_symbol: str, strike: float) -> str | None:
    """The futures contract matching an option's underlying and expiry.

    Built from the option's own symbol - "NSE:NIFTY26OCT23100PE" becomes
    "NSE:NIFTY26OCTFUT" - rather than assembled from an index name, because
    the index and its derivatives are not named alike: NIFTYBANK-INDEX trades
    options as BANKNIFTY. Taking the prefix the exchange already used avoids
    having to know that.

    The strike is passed in rather than guessed at. Stripping trailing digits
    works for a monthly ("...26OCT23100PE") but eats the day out of a weekly
    ("...26O0623100PE" became "...26O"), because the expiry ends in digits
    too. We already know the strike, so remove exactly that.
    """
    for suffix in ("CE", "PE"):
        if not option_symbol.endswith(suffix):
            continue
        head = option_symbol[: -len(suffix)]
        for text in _strike_spellings(strike):
            if head.endswith(text) and len(head) > len(text):
                return head[: -len(text)] + "FUT"
    return None


def _strike_spellings(strike: float) -> list[str]:
    """How a strike might appear in a symbol, most likely first."""
    out = []
    if strike == int(strike):
        out.append(str(int(strike)))
    text = f"{strike:g}"
    if text not in out:
        out.append(text)
    return out

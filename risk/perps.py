"""Pre-trade checks for a leveraged order.

The options desk risks a defined amount: a spread's worst case is known before
it is placed, and the checks in `risk/limits.py` are about whether that amount is
acceptable. A perpetual has no such number. At 75x, a move of one and a third
percent is the whole margin, and the position closes itself - so the checks here
are about size and distance rather than a worst case, because there is no worst
case to compute.

Every one of them is a seatbelt against this program being wrong, not against the
market. A market that moves against a sized position is trading; an order for a
hundred times the intended size is a bug, and these exist to stop a bug becoming
a position.
"""

from __future__ import annotations

from dataclasses import dataclass

from risk.result import RiskCheckResult

#: How close to liquidation a new position may start. A position opened inside
#: this is one the market can close on noise alone.
MIN_LIQUIDATION_DISTANCE = 0.02


@dataclass(frozen=True)
class PerpLimits:
    """Caps on what this program may do, per instrument.

    Deliberately not "what is a sensible trade". A cap here says "beyond this,
    assume the software is wrong", which is why they are small: the cost of a cap
    being too tight is an order refused and retyped, and the cost of it being too
    loose is unbounded.
    """

    #: Largest notional in one order, quantity times price, in the quote asset.
    #:
    #: The cap that means something. It is in money, so one number applies to
    #: every instrument, and it catches the mistake a quantity cap is meant to
    #: catch - 0.002 typed as 2 is 168,000 of notional and refused here.
    max_notional: float
    #: Largest quantity in one order, in contracts. Off by default, and it should
    #: usually stay off.
    #:
    #: A quantity cannot be compared across these instruments: 0.01 BTCUSDT is
    #: about 840 USDT while 0.01 CLUSDT is 94 cents, three orders of magnitude
    #: apart. A single figure tight enough to be useful on Bitcoin blocks the
    #: smallest legal order in oil, which is what happened - oil's minimum is 0.07
    #: against a cap of 0.01, so the contract was untradeable. Zero means no cap.
    max_quantity: float = 0.0
    #: An optional ceiling of your own, on top of the venue's per-contract
    #: maximum. Zero means "no ceiling of ours" - the venue's limit still applies,
    #: since it is the one that would reject the order.
    max_leverage: float = 0.0


def check_quantity(quantity: float, limit: float) -> RiskCheckResult:
    """A cap in contracts, if one is set at all. See `PerpLimits.max_quantity`."""
    if quantity <= 0:
        return RiskCheckResult(False, f"Quantity {quantity} is not a size")
    if limit <= 0:
        return RiskCheckResult(True, "No quantity cap set; notional is the cap")
    if quantity > limit:
        return RiskCheckResult(
            False, f"Quantity {quantity:g} is past the {limit:g} cap for one order"
        )
    return RiskCheckResult(True, f"Quantity {quantity:g} is within the {limit:g} cap")


def check_notional(quantity: float, price: float, limit: float) -> RiskCheckResult:
    """Size in money, which is the cap that survives a price change.

    A quantity cap alone is not enough: 0.01 BTC is a different amount of money
    at 20,000 than at 120,000, and a cap written when one was true does not hold
    when the other is.
    """
    if price <= 0:
        return RiskCheckResult(False, "No price to size this against")
    notional = quantity * price
    if notional > limit:
        return RiskCheckResult(
            False, f"Notional {notional:,.0f} is past the {limit:,.0f} cap for one order"
        )
    return RiskCheckResult(True, f"Notional {notional:,.0f} is within the {limit:,.0f} cap")


def check_leverage(leverage: float, venue_max: float, own_ceiling: float = 0.0) -> RiskCheckResult:
    """Against the venue's maximum for this contract, and any ceiling of your own.

    The venue's number is per contract and differs sharply - 150x on BTCUSDT, 75x
    on gold, 50x on oil - so a single figure written here would either block a
    legitimate order or wave through one the venue rejects. It is asked for rather
    than remembered.
    """
    if leverage <= 0:
        return RiskCheckResult(False, f"Leverage {leverage} is not a multiple")
    if venue_max > 0 and leverage > venue_max:
        return RiskCheckResult(
            False, f"Leverage {leverage:g}x is past the venue's {venue_max:g}x for this contract"
        )
    if own_ceiling > 0 and leverage > own_ceiling:
        return RiskCheckResult(False, f"Leverage {leverage:g}x is past your own {own_ceiling:g}x")
    return RiskCheckResult(True, f"Leverage {leverage:g}x is allowed here")


def check_liquidation_distance(
    entry: float, liquidation: float | None, minimum: float = MIN_LIQUIDATION_DISTANCE
) -> RiskCheckResult:
    """How much room the position starts with.

    The check that has no options equivalent. A spread cannot be closed against
    you by a small move; a leveraged position can, and one opened two percent from
    liquidation is one the market ends on noise.

    Unknown is not treated as a failure: the venue computes the real figure from
    the margin mode and its own maintenance rules, and an estimate refusing an
    order the venue would accept is its own kind of wrong. It is reported as
    unknown, which is what the review screen shows.
    """
    if liquidation is None or entry <= 0:
        return RiskCheckResult(True, "Liquidation price not known before placing")
    distance = abs(entry - liquidation) / entry
    if distance < minimum:
        return RiskCheckResult(
            False,
            f"Liquidation is {distance:.1%} away, inside the {minimum:.0%} minimum",
        )
    return RiskCheckResult(True, f"Liquidation is {distance:.1%} away")


@dataclass(frozen=True)
class PerpOrderCheck:
    """Every check on one order, and whether it may be sent."""

    checks: list[RiskCheckResult]
    #: What the order is worth, for the review screen.
    notional: float
    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def reasons(self) -> list[str]:
        return [check.reason for check in self.checks if not check.passed]


def check_minimum(quantity: float, smallest: float) -> RiskCheckResult:
    """Whether the venue would accept a size this small.

    A floor rather than a cap, and the only check here that protects you from a
    rejection rather than from a mistake. Worth running anyway: the venue's answer
    is "order failed" with no arithmetic, and the real floor moves with the price
    because it is a notional minimum.
    """
    if smallest <= 0:
        return RiskCheckResult(True, "No minimum known for this contract")
    if quantity < smallest:
        return RiskCheckResult(
            False, f"Quantity {quantity:g} is under the {smallest:g} minimum for this contract"
        )
    return RiskCheckResult(True, f"Quantity {quantity:g} is at or above the {smallest:g} minimum")


def check_perp_order(
    quantity: float,
    price: float,
    leverage: float,
    limits: PerpLimits,
    liquidation: float | None = None,
    venue_max_leverage: float = 0.0,
    smallest_order: float = 0.0,
) -> PerpOrderCheck:
    """Run every check. Order matters only for reading the result."""
    checks = [
        check_quantity(quantity, limits.max_quantity),
        check_minimum(quantity, smallest_order),
        check_notional(quantity, price, limits.max_notional),
        check_leverage(leverage, venue_max_leverage, limits.max_leverage),
        check_liquidation_distance(price, liquidation),
    ]
    return PerpOrderCheck(checks=checks, notional=quantity * max(price, 0.0))

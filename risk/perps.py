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

    #: Largest quantity in one order, in contracts.
    max_quantity: float
    #: Largest notional in one order, quantity times price, in the quote asset.
    max_notional: float
    #: Highest leverage this program will use, whatever the venue allows. The
    #: venue permits 150x on some contracts; that is not an invitation.
    max_leverage: float
    #: When true, orders are formed, checked and recorded but never sent.
    dry_run: bool


def check_quantity(quantity: float, limit: float) -> RiskCheckResult:
    if quantity <= 0:
        return RiskCheckResult(False, f"Quantity {quantity} is not a size")
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


def check_leverage(leverage: float, limit: float) -> RiskCheckResult:
    if leverage <= 0:
        return RiskCheckResult(False, f"Leverage {leverage} is not a multiple")
    if leverage > limit:
        return RiskCheckResult(False, f"Leverage {leverage:g}x is past the {limit:g}x cap")
    return RiskCheckResult(True, f"Leverage {leverage:g}x is within the {limit:g}x cap")


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
    #: True when the checks passed but the order will be recorded rather than
    #: sent. Held apart from `passed` so a client cannot mistake "we did not send
    #: this" for "this was refused".
    dry_run: bool

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def reasons(self) -> list[str]:
        return [check.reason for check in self.checks if not check.passed]


def check_perp_order(
    quantity: float,
    price: float,
    leverage: float,
    limits: PerpLimits,
    liquidation: float | None = None,
) -> PerpOrderCheck:
    """Run every check. Order matters only for reading the result."""
    checks = [
        check_quantity(quantity, limits.max_quantity),
        check_notional(quantity, price, limits.max_notional),
        check_leverage(leverage, limits.max_leverage),
        check_liquidation_distance(price, liquidation),
    ]
    return PerpOrderCheck(
        checks=checks,
        notional=quantity * max(price, 0.0),
        dry_run=limits.dry_run,
    )

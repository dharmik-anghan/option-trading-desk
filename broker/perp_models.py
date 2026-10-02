"""Perpetual futures: a leveraged position, and what a venue will accept.

Venue-neutral, like `broker/models.py`; any perpetuals adapter returns these.

A leveraged position is one the shared `Position` cannot describe.

`broker/models.Position` is an options position: a symbol, a signed quantity, an
average price and a P&L. A perpetual adds the things that decide whether it
survives - leverage, the margin behind it, and the price at which the venue
closes it for you - and none of those have an equivalent on the options side.

So this is its own model rather than fields bolted onto that one, for the same
reason the broker protocols are split: an options position with a liquidation
price of None is a position that has been made to answer a question it does not
have.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class PerpPosition:
    """One open perpetual futures position."""

    symbol: str
    #: LONG or SHORT, as the venue words it. Held rather than inferred from a
    #: signed quantity, because the venue reports SHORT with a positive size and
    #: reconstructing the sign to then re-derive the side loses nothing but adds
    #: a step that can be wrong.
    side: str
    #: Always positive. `side` carries the direction.
    quantity: float
    entry_price: float
    #: What the venue values it at now. None when it does not say, in which case
    #: the desk prices it from the tick stream instead.
    mark_price: float | None
    leverage: float
    #: The price at which the venue closes this for you. The single most
    #: important number on a leveraged position and the reason this model exists.
    liquidation_price: float | None
    #: CROSS or ISOLATED. Decides whether the rest of the account backs this
    #: position or only the margin posted against it.
    margin_type: str
    #: Margin posted, in the *quote* currency - USDT on this venue, despite the
    #: field standing next to `margin_asset`. The venue reports both: `margin`
    #: in USDT and `marginInMarginAsset` in INR, and a position showing 0.862
    #: margin labelled INR was out by the conversion rate of 102. See
    #: contract quoted in USDT.
    margin: float
    #: The same figure in the margin asset, which is what the account is actually
    #: debited. None when the venue did not report it.
    margin_in_margin_asset: float | None
    margin_asset: str
    #: Unrealised P&L in the quote asset, when the venue reports it.
    unrealized_pnl: float | None
    #: The same figure in the margin asset, when the venue reports both. It does
    #: for a closed position, alongside the rate it converted at.
    unrealized_pnl_in_margin_asset: float | None
    #: The venue's own id, for cancelling or attaching a stop to this position.
    position_id: str
    #: How many take-profit and stop-loss orders the venue is already holding
    #: against this position. Counts, not levels - the position payload reports
    #: only how many, and reading the levels means listing open orders.
    #:
    #: Zero stop-loss orders on a leveraged position is the thing worth noticing
    #: on this desk, which is why it is carried here rather than fetched when
    #: somebody thinks to look.
    take_profit_orders: int = 0
    stop_loss_orders: int = 0
    #: What one unit of the quote currency is worth in the margin asset - INR per
    #: USDT on this venue. Carried because the desk works profit out live from a
    #: streamed price, and a figure in USDT beside margin in INR is two
    #: currencies on one line. None when the venue did not say.
    conversion_rate: float | None = None

    @property
    def is_protected(self) -> bool:
        """Whether the venue is holding a stop for this position.

        A stop the exchange holds works with this app closed and the machine off,
        which is the only kind that counts on a market that trades overnight.
        """
        return self.stop_loss_orders > 0

    @property
    def is_long(self) -> bool:
        return self.side.upper() == "LONG"

    def liquidation_distance(self, price: float | None) -> float | None:
        """How far this is from being closed for you, as a fraction of price.

        The number to look at on a leveraged position, and the reason it is a
        fraction rather than a difference: 200 points from liquidation means one
        thing on gold at 4,300 and another on Bitcoin at 84,000.

        Always positive when the position is alive - a long is liquidated below
        and a short above, so the direction is not information.
        """
        if price is None or not price or self.liquidation_price is None:
            return None
        return abs(price - self.liquidation_price) / price


@dataclass(frozen=True)
class ContractSpec:
    """What the venue will accept for one contract.

    Fetched rather than written down, because these are the venue's rules and a
    copy of them in this repository is a copy that goes stale silently. They
    change rarely, so the fetch is cached for a day.

    The interesting one is `min_notional`. A minimum quantity looks like the floor
    and usually is not: BTCUSDT allows 0.001 but demands 115 USDT of notional, so
    at 84,000 the smallest real order is 0.002 - and the figure moves with the
    price, which is why it has to be computed rather than remembered.
    """

    symbol: str
    max_leverage: float
    #: Smallest quantity the venue accepts, before the notional floor is applied.
    min_quantity: float
    #: Smallest order value. Usually the binding constraint, and price-dependent.
    min_notional: float
    price_dp: int
    quantity_dp: int
    #: Percentage of notional that must remain as margin. Higher means liquidated
    #: sooner: oil is 35% where crypto and gold are 15%.
    maintenance_margin_pct: float

    def smallest_order(self, price: float) -> float:
        """The smallest quantity that satisfies both floors at this price.

        Rounded up to the venue's quantity precision, because rounding down
        produces a number the venue rejects - which is the whole point of asking.
        """
        if price <= 0:
            return self.min_quantity
        step = 10.0**-self.quantity_dp
        needed = max(self.min_quantity, self.min_notional / price)
        steps = math.ceil(round(needed / step, 6))
        return round(steps * step, self.quantity_dp)

    def margin_required(self, quantity: float, price: float, leverage: float) -> float | None:
        """What the position costs to hold, in the quote asset.

        Notional over leverage. Not what the venue will charge to the rupee - it
        margins in INR at a rate it decides, and adds a buffer - so this is the
        size of the commitment rather than a quotation.
        """
        if leverage <= 0 or price <= 0:
            return None
        return (quantity * price) / leverage

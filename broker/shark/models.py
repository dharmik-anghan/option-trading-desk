"""A leveraged position, which the shared `Position` cannot describe.

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
    #: Margin posted, in the margin asset - INR on this venue, even for a
    #: contract quoted in USDT.
    margin: float
    margin_asset: str
    #: Unrealised P&L in the quote asset, when the venue reports it.
    unrealized_pnl: float | None
    #: The same figure in the margin asset, when the venue reports both. It does
    #: for a closed position, alongside the rate it converted at.
    unrealized_pnl_in_margin_asset: float | None
    #: The venue's own id, for cancelling or attaching a stop to this position.
    position_id: str

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

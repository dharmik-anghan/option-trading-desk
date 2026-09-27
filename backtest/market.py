"""What differs between a perpetual, an option and a share.

The loop that walks bars, the rules that read them and the metrics that judge the
result are the same whatever is being traded. Four things are not: what a position
is worth, what it costs to open and close, what it costs to hold, and how it can
end without you. Those live behind `Market`, so adding an instrument means writing
one of these rather than editing the engine.

It is worth being concrete about how differently the four behave, because it is
the reason for the seam:

  A perpetual is marked continuously, charges a symmetric percentage fee on both
  sides, pays or receives funding every few hours, and can be closed by the venue
  when the margin runs out.

  An Indian option or future charges a fee that is *not* symmetric - securities
  transaction tax falls on one side only - has no funding but a premium that
  decays on its own, cannot be liquidated if bought, and stops existing on a
  date.

  A share has no expiry and no liquidation when paid for in full, and carries a
  borrow cost only when sold short.

A market computes money. It does not decide whether a trade is a good idea, and it
holds no opinion about strategy.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class Side(StrEnum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        """+1 for a long, -1 for a short. The factor in every P&L expression."""
        return 1 if self is Side.LONG else -1

    @property
    def opposite(self) -> Side:
        return Side.SHORT if self is Side.LONG else Side.LONG


class Market(Protocol):
    """One tradeable instrument, and what it costs to use."""

    symbol: str
    #: What prices and profits are denominated in.
    quote: str
    #: Units per unit of quantity. One for a perpetual quoted in coins; where an
    #: option's or a future's lot size goes.
    multiplier: float
    #: Smallest quantity the venue accepts.
    min_quantity: float
    #: Smallest order value, in the quote currency. Usually the binding one: a
    #: venue can allow 0.001 BTC and still demand 115 USDT of it.
    min_notional: float

    def round_quantity(self, quantity: float) -> float:
        """Down to the venue's step. A size it would not accept is not a size."""
        ...

    def notional(self, quantity: float, price: float) -> float:
        """What a quantity is worth at a price, in the quote currency."""
        ...

    def fee(
        self, quantity: float, price: float, *, maker: bool, opening: bool, side: Side
    ) -> float:
        """The cost of one fill.

        Takes `opening` and `side` because not every venue charges symmetrically:
        Indian securities transaction tax falls on the sell leg alone, so a round
        trip costs differently depending on which way round it was.
        """
        ...

    def carry(
        self, side: Side, quantity: float, frm: datetime, to: datetime, mark: float
    ) -> float:
        """The cost of holding across an interval. Positive means money out."""
        ...

    def liquidation(self, side: Side, entry: float, leverage: float) -> float | None:
        """The price at which the position is closed for you, if that can happen."""
        ...

    def margin(self, quantity: float, price: float, leverage: float) -> float:
        """What must be posted to hold this position."""
        ...


# ---------------------------------------------------------------------------
# Perpetual futures
# ---------------------------------------------------------------------------

#: Goods and services tax on the exchange's fee. Charged on top of both the maker
#: and the taker rate, so the advertised numbers are 18% short of what is paid -
#: which turns a 0.040% taker fee into 0.0472% and a round trip into 9.4 basis
#: points. Large enough on a short-horizon rule to decide whether it works.
GST = 0.18


@dataclass(frozen=True)
class FundingSchedule:
    """Settlements and their rates, in time order.

    A rate is a fraction of notional charged at that instant: positive means longs
    pay shorts. Held sorted so a lookup over a bar is two binary searches rather
    than a scan of three thousand settlements per bar.
    """

    #: Where these came from. Kept because the venue this desk trades publishes no
    #: funding history at all, so these are Binance's - a proxy that has to be
    #: named wherever a result depends on it.
    source: str
    times: tuple[datetime, ...] = ()
    rates: tuple[float, ...] = ()

    @classmethod
    def of(cls, source: str, rows: Sequence[tuple[datetime, float]]) -> FundingSchedule:
        ordered = sorted(rows)
        return cls(
            source=source,
            times=tuple(at for at, _ in ordered),
            rates=tuple(rate for _, rate in ordered),
        )

    def between(self, frm: datetime, to: datetime) -> list[float]:
        """Rates settling in (frm, to].

        Half-open at the start so a position opened exactly on a settlement does
        not pay for the moment before it existed, and closed at the end so one
        held right through a settlement does pay.
        """
        return list(self.rates[bisect_right(self.times, frm) : bisect_right(self.times, to)])


@dataclass
class PerpetualMarket:
    """A perpetual future, as Shark charges for one.

    Fees come from their published schedule; funding comes from whatever schedule
    it is given, because they publish none. Liquidation is computed from the
    maintenance margin their contract data reports, which is the one number in
    here that is fetched from the venue rather than written down.
    """

    symbol: str
    quote: str = "USDT"
    #: Published rates, before tax. Defaults are Shark's.
    maker_fee: float = 0.00016
    taker_fee: float = 0.00040
    #: Fraction of notional that must remain as margin. Their contract data gives
    #: 15% for crypto and gold, 35% for oil.
    maintenance_margin: float = 0.15
    funding: FundingSchedule | None = None
    #: Contracts per unit of quantity. One for a perpetual quoted in coins; the
    #: place an option's lot size goes.
    multiplier: float = 1.0
    #: Shark's floors for BTCUSDT, which is the only pair with stored history.
    #: Fetched per contract by the desk; written down here so a backtest of an
    #: instrument nobody has fetched still refuses a size nobody could place.
    min_quantity: float = 0.001
    min_notional: float = 0.0
    #: Decimal places the venue accepts in a quantity.
    quantity_dp: int = 3

    def round_quantity(self, quantity: float) -> float:
        """Down to the venue's step, never up.

        Rounding up can put an order above the margin that was just checked for
        it. Being one step small costs a rounding error; being one step large
        costs a rejection.
        """
        step = 10.0**-self.quantity_dp
        return math.floor(quantity / step) * step

    def notional(self, quantity: float, price: float) -> float:
        return abs(quantity) * price * self.multiplier

    def fee(
        self, quantity: float, price: float, *, maker: bool, opening: bool, side: Side
    ) -> float:
        """Symmetric: the same rate whichever way and whichever side.

        `opening` and `side` are ignored here and present because the protocol
        needs them for instruments where they matter.
        """
        rate = self.maker_fee if maker else self.taker_fee
        return self.notional(quantity, price) * rate * (1 + GST)

    def carry(
        self, side: Side, quantity: float, frm: datetime, to: datetime, mark: float
    ) -> float:
        """Funding settled while the position was open.

        Charged on notional at the mark, which is what a venue does. A positive
        rate is longs paying shorts, so a long pays and a short is paid - hence the
        sign, and hence a short in a market where funding is usually positive is
        being paid to wait.
        """
        if self.funding is None:
            return 0.0
        rates = self.funding.between(frm, to)
        if not rates:
            return 0.0
        return self.notional(quantity, mark) * sum(rates) * side.sign

    def liquidation(self, side: Side, entry: float, leverage: float) -> float | None:
        """Where the position closes itself.

        The margin posted is 1/leverage of notional, and the position is closed
        when what is left falls to the maintenance requirement. So the move that
        kills it is (1/leverage - maintenance) of the entry price - about 1.3% at
        75x on crypto, which is why a leveraged rule that ignores this is not
        describing anything that could have been traded.

        None when the maintenance requirement is already above the margin posted:
        the venue would not accept the position at all.
        """
        if leverage <= 0:
            return None
        room = 1.0 / leverage - self.maintenance_margin
        if room <= 0:
            return None
        return entry * (1 - room * side.sign)

    def margin(self, quantity: float, price: float, leverage: float) -> float:
        if leverage <= 0:
            raise ValueError("leverage must be positive")
        return self.notional(quantity, price) / leverage


@dataclass(frozen=True)
class Costs:
    """Where the money went, kept apart so a result can say.

    Separated because the answer is usually "the costs". A rule whose gross profit
    is real and whose net is negative needs a different trade size or a longer
    horizon; one whose gross is negative needs a different idea. A single net
    figure cannot tell those two apart.
    """

    fees: float = 0.0
    funding: float = 0.0
    slippage: float = 0.0

    @property
    def total(self) -> float:
        return self.fees + self.funding + self.slippage

    def plus(self, *, fees: float = 0.0, funding: float = 0.0, slippage: float = 0.0) -> Costs:
        return Costs(
            fees=self.fees + fees,
            funding=self.funding + funding,
            slippage=self.slippage + slippage,
        )

"""Iron condor: a short strangle with protective long wings further OTM
(lower delta). Unlike the naked short strangle, this caps max loss at the
wing width minus net credit received.
"""

from __future__ import annotations

from dataclasses import dataclass

from analytics.payoff import Leg
from broker.models import OptionChain
from strategies.base import Strategy
from strategies.selection import select_by_delta


@dataclass
class IronCondor(Strategy):
    short_delta: float = 0.16
    long_delta: float = 0.08
    quantity: int = 1
    name: str = "iron_condor"

    def build_legs(self, chain: OptionChain) -> list[Leg]:
        short_call = select_by_delta(chain, "CE", self.short_delta)
        long_call = select_by_delta(chain, "CE", self.long_delta)
        short_put = select_by_delta(chain, "PE", self.short_delta)
        long_put = select_by_delta(chain, "PE", self.long_delta)

        return [
            Leg(
                option_type="CE",
                strike=short_call.strike,
                premium=short_call.ltp,
                quantity=self.quantity,
                side="SELL",
            ),
            Leg(
                option_type="CE",
                strike=long_call.strike,
                premium=long_call.ltp,
                quantity=self.quantity,
                side="BUY",
            ),
            Leg(
                option_type="PE",
                strike=short_put.strike,
                premium=short_put.ltp,
                quantity=self.quantity,
                side="SELL",
            ),
            Leg(
                option_type="PE",
                strike=long_put.strike,
                premium=long_put.ltp,
                quantity=self.quantity,
                side="BUY",
            ),
        ]

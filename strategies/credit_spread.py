"""Credit spread: a directional, defined-risk two-leg trade.

Bullish (bull put spread): sell a higher-strike put, buy a lower-strike put.
Bearish (bear call spread): sell a lower-strike call, buy a higher-strike call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from analytics.payoff import Leg
from broker.models import OptionChain, OptionType
from strategies.base import Strategy
from strategies.selection import select_by_delta

Direction = Literal["bullish", "bearish"]


@dataclass
class CreditSpread(Strategy):
    direction: Direction
    short_delta: float = 0.20
    long_delta: float = 0.10
    quantity: int = 1
    name: str = "credit_spread"

    def build_legs(self, chain: OptionChain) -> list[Leg]:
        option_type: OptionType = "PE" if self.direction == "bullish" else "CE"
        short_row = select_by_delta(chain, option_type, self.short_delta)
        long_row = select_by_delta(chain, option_type, self.long_delta)

        return [
            Leg(
                option_type=option_type,
                strike=short_row.strike,
                premium=short_row.ltp,
                quantity=self.quantity,
                side="SELL",
            ),
            Leg(
                option_type=option_type,
                strike=long_row.strike,
                premium=long_row.ltp,
                quantity=self.quantity,
                side="BUY",
            ),
        ]

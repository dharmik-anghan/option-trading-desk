"""Short strangle: sell an OTM call and an OTM put at (roughly) the same
delta. Naked on both sides — max loss is unbounded, which
`analytics.payoff.analyze` surfaces as `-math.inf` rather than a finite
number, exactly the risk callers need to see before confirming this trade.
"""

from __future__ import annotations

from dataclasses import dataclass

from analytics.payoff import Leg
from broker.models import OptionChain
from strategies.base import Strategy
from strategies.selection import select_by_delta


@dataclass
class ShortStrangle(Strategy):
    target_delta: float = 0.16
    quantity: int = 1
    name: str = "short_strangle"

    def build_legs(self, chain: OptionChain) -> list[Leg]:
        call_row = select_by_delta(chain, "CE", self.target_delta)
        put_row = select_by_delta(chain, "PE", self.target_delta)
        return [
            Leg(
                option_type="CE",
                strike=call_row.strike,
                premium=call_row.ltp,
                quantity=self.quantity,
                side="SELL",
            ),
            Leg(
                option_type="PE",
                strike=put_row.strike,
                premium=put_row.ltp,
                quantity=self.quantity,
                side="SELL",
            ),
        ]

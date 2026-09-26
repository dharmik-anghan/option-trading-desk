"""Live portfolio P&L: what's actually open, and today's total P&L.

Fyers already computes both halves for us - `unrealized_profit` per
position, and `Realized Profit and Loss` in the funds response - so this is
aggregation, not calculation. What Phase 6 adds is combining them into one
number the kill-switch (`risk.limits.check_daily_kill_switch`) can act on.
"""

from __future__ import annotations

from dataclasses import dataclass

from broker.base import FundedBroker
from broker.models import Position


@dataclass(frozen=True)
class PortfolioStatus:
    positions: list[Position]
    realized_pnl: float
    unrealized_pnl: float

    @property
    def total_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl


def get_portfolio_status(broker: FundedBroker) -> PortfolioStatus:
    positions = broker.get_positions()
    funds = broker.get_funds()
    unrealized_pnl = sum(p.unrealized_pnl for p in positions)
    return PortfolioStatus(
        positions=positions, realized_pnl=funds.realized_pnl, unrealized_pnl=unrealized_pnl
    )

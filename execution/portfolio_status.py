"""Live portfolio P&L: what's actually open, and today's total P&L.

Fyers already computes both halves for us - `unrealized_profit` per
position, and `Realized Profit and Loss` in the funds response - so this is
aggregation, not calculation. What Phase 6 adds is combining them into one
number the kill-switch (`risk.limits.check_daily_kill_switch`) can act on.
"""

from __future__ import annotations

from dataclasses import dataclass

from broker.base import BookedPnl, FundedBroker
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
    """Booked comes from the venue's positions where it reports it there.

    Fyers' funds figure for realized P&L stayed at 0 through a day on which a
    spread was closed for +4,881.50 - so the desk showed nothing booked, and the
    daily kill-switch read the day as 4,881.50 worse than it was. The positions
    response had the right number all along. Funds remains the fallback for a
    venue that only reports it there.
    """
    positions = broker.get_positions()
    unrealized_pnl = sum(p.unrealized_pnl for p in positions)
    if isinstance(broker, BookedPnl):
        realized = broker.get_booked_pnl()
    else:
        realized = broker.get_funds().realized_pnl
    return PortfolioStatus(
        positions=positions, realized_pnl=realized, unrealized_pnl=unrealized_pnl
    )

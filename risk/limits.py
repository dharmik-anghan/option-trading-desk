"""Max-loss-per-trade check and the daily-loss kill-switch.

`check_daily_kill_switch` takes today's P&L as an input rather than
computing it — live P&L tracking is Phase 6. This is the check Phase 6's
tracker (and Phase 5's execution loop) will call each time it has a fresh
number, not something that fetches its own state.
"""

from __future__ import annotations

import math

from risk.result import RiskCheckResult


def check_max_loss_limit(max_loss: float, limit: float) -> RiskCheckResult:
    """`max_loss` and `limit` are both loss magnitudes as negative numbers
    (e.g. `analytics.payoff.PayoffResult.max_loss`); `limit` is the largest
    loss you're willing to accept for one trade.
    """
    if math.isinf(max_loss):
        return RiskCheckResult(
            passed=False, reason="Max loss is unbounded, which no finite limit can satisfy"
        )
    if max_loss < -abs(limit):
        return RiskCheckResult(
            passed=False, reason=f"Max loss {max_loss} exceeds the per-trade limit of {-abs(limit)}"
        )
    return RiskCheckResult(
        passed=True, reason=f"Max loss {max_loss} is within the per-trade limit of {-abs(limit)}"
    )


def check_daily_kill_switch(
    realized_and_unrealized_pnl_today: float, daily_loss_limit: float
) -> RiskCheckResult:
    """`daily_loss_limit` is a positive magnitude: the most you're willing to
    lose today before new entries are blocked.
    """
    if realized_and_unrealized_pnl_today < -abs(daily_loss_limit):
        return RiskCheckResult(
            passed=False,
            reason=(
                f"Today's P&L {realized_and_unrealized_pnl_today} has breached the daily loss "
                f"limit of {-abs(daily_loss_limit)} - new entries are blocked"
            ),
        )
    return RiskCheckResult(
        passed=True,
        reason=f"Today's P&L {realized_and_unrealized_pnl_today} is within the daily loss limit",
    )

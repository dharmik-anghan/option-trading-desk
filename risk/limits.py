"""Max-loss-per-trade check and the daily-loss kill-switch.

`check_daily_kill_switch` takes today's P&L as an input rather than
computing it — live P&L tracking is Phase 6. This is the check Phase 6's
tracker (and Phase 5's execution loop) will call each time it has a fresh
number, not something that fetches its own state.
"""

from __future__ import annotations

import math

from analytics.payoff import PayoffResult
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


def check_position_is_real(payoff: PayoffResult) -> RiskCheckResult:
    """Reject a structure that has no exposure at all.

    A strike-selection failure can return offsetting legs - the same strike
    bought and sold - which nets to zero profit and zero loss. Every other
    check passes such a position happily: nothing to lose is trivially within
    any loss limit. But placing it still sends every leg to the exchange and
    pays brokerage and slippage on all of them, for no position.

    This is deliberately a check rather than only a strategy-side assertion,
    so a bug in any strategy cannot reach the exchange through this gate.
    """
    duplicates = _offsetting_legs(payoff)
    if duplicates:
        listed = ", ".join(duplicates)
        return RiskCheckResult(
            passed=False,
            reason=(
                f"These legs cancel out, leaving no position: {listed}. "
                "Usually the strike window is too narrow for the deltas asked for."
            ),
        )
    if payoff.max_profit == 0 and payoff.max_loss == 0:
        return RiskCheckResult(
            passed=False,
            reason="This structure cannot gain or lose anything - there is no position to take",
        )
    return RiskCheckResult(passed=True, reason="The structure has real exposure")


def _offsetting_legs(payoff: PayoffResult) -> list[str]:
    """Strike/type pairs held both long and short in the same quantity."""
    net: dict[tuple[str, float], int] = {}
    seen: dict[tuple[str, float], int] = {}
    for leg in payoff.legs:
        key = (leg.option_type, leg.strike)
        signed = leg.quantity if leg.side == "BUY" else -leg.quantity
        net[key] = net.get(key, 0) + signed
        seen[key] = seen.get(key, 0) + 1
    return [
        f"{int(strike)} {option_type}"
        for (option_type, strike), total in sorted(net.items(), key=lambda kv: kv[0][1])
        if total == 0 and seen[(option_type, strike)] > 1
    ]

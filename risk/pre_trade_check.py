"""The consolidated pre-trade risk gate: what Phase 5's confirm screen calls
before letting you place an order. Ties together margin, per-trade max-loss,
and position sizing into one result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from analytics.payoff import PayoffResult
from risk.limits import check_max_loss_limit
from risk.margin import check_sufficient_margin
from risk.result import RiskCheckResult
from risk.sizing import max_quantity_for_risk

# Shared defaults for callers (CLI scripts, the dashboard API) that don't
# have a real per-order margin figure yet - see docs/GLOSSARY.md's
# "Required margin" entry for the known gap this placeholder covers.
DEFAULT_REQUIRED_MARGIN_PLACEHOLDER = 50000.0
DEFAULT_MAX_RISK_PCT = 2.0
DEFAULT_MAX_LOSS_LIMIT = 5000.0


@dataclass(frozen=True)
class PreTradeCheckResult:
    checks: list[RiskCheckResult]
    max_quantity: int

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


def run_pre_trade_checks(
    payoff: PayoffResult,
    available_funds: float,
    required_margin: float,
    capital: float,
    max_risk_pct: float,
    max_loss_limit: float,
) -> PreTradeCheckResult:
    checks = [
        check_sufficient_margin(available_funds, required_margin),
        check_max_loss_limit(payoff.max_loss, max_loss_limit),
    ]

    max_quantity = 0
    if math.isfinite(payoff.max_loss) and payoff.max_loss < 0:
        max_quantity = max_quantity_for_risk(capital, max_risk_pct, abs(payoff.max_loss))

    return PreTradeCheckResult(checks=checks, max_quantity=max_quantity)

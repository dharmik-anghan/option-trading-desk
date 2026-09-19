"""Margin sufficiency check.

Note: `required_margin` is supplied by the caller, not computed here.
Fyers' Python SDK (fyers-apiv3) exposes account funds (`Broker.get_funds`)
but no pre-trade SPAN/exposure margin calculator, so an accurate
per-strategy margin requirement isn't available yet from this broker. See
docs/GLOSSARY.md for the tracked gap.
"""

from __future__ import annotations

from risk.result import RiskCheckResult


def check_sufficient_margin(available_funds: float, required_margin: float) -> RiskCheckResult:
    if available_funds >= required_margin:
        return RiskCheckResult(
            passed=True,
            reason=f"Available funds {available_funds} cover required margin {required_margin}",
        )
    return RiskCheckResult(
        passed=False,
        reason=f"Available funds {available_funds} are less than required margin {required_margin}",
    )

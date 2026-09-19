"""Note: Fyers' Python SDK (fyers-apiv3) doesn't expose a pre-trade SPAN/
exposure margin calculator - only account funds. So `required_margin` here
is whatever the caller supplies (e.g. a conservative manual estimate, or
later a real margin-calculator API for a broker that has one), not something
this module computes itself. See docs/GLOSSARY.md.
"""

from __future__ import annotations

from risk.margin import check_sufficient_margin


def test_passes_when_available_covers_required() -> None:
    result = check_sufficient_margin(available_funds=50000, required_margin=20000)

    assert result.passed is True


def test_fails_when_required_exceeds_available() -> None:
    result = check_sufficient_margin(available_funds=10000, required_margin=20000)

    assert result.passed is False
    assert "10000" in result.reason
    assert "20000" in result.reason


def test_passes_at_exact_boundary() -> None:
    result = check_sufficient_margin(available_funds=20000, required_margin=20000)

    assert result.passed is True

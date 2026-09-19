"""A naked short strangle and a defined-risk iron condor built from the same
chain must surface very different risk numbers — this is exactly the
pre-trade check the semi-auto confirm flow (Phase 5) depends on.
"""

from __future__ import annotations

import math

from analytics.payoff import analyze
from broker.models import OptionChain
from strategies.credit_spread import CreditSpread
from strategies.iron_condor import IronCondor
from strategies.short_strangle import ShortStrangle


def test_short_strangle_has_unbounded_max_loss(sample_chain: OptionChain) -> None:
    legs = ShortStrangle(target_delta=0.16).build_legs(sample_chain)

    result = analyze(legs)

    assert result.max_loss == -math.inf


def test_iron_condor_has_finite_capped_max_loss(sample_chain: OptionChain) -> None:
    legs = IronCondor(short_delta=0.16, long_delta=0.08).build_legs(sample_chain)

    result = analyze(legs)

    assert math.isfinite(result.max_loss)
    assert math.isfinite(result.max_profit)


def test_credit_spread_has_finite_capped_max_loss(sample_chain: OptionChain) -> None:
    legs = CreditSpread(direction="bullish", short_delta=0.16, long_delta=0.08).build_legs(
        sample_chain
    )

    result = analyze(legs)

    assert math.isfinite(result.max_loss)
    assert math.isfinite(result.max_profit)

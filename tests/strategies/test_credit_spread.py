from __future__ import annotations

from broker.models import OptionChain
from strategies.credit_spread import CreditSpread


def test_bullish_credit_spread_uses_puts(sample_chain: OptionChain) -> None:
    strategy = CreditSpread(direction="bullish", short_delta=0.16, long_delta=0.08)

    legs = strategy.build_legs(sample_chain)

    assert len(legs) == 2
    assert all(leg.option_type == "PE" for leg in legs)
    by_strike = {leg.strike: leg for leg in legs}
    assert by_strike[80].side == "SELL"
    assert by_strike[70].side == "BUY"


def test_bearish_credit_spread_uses_calls(sample_chain: OptionChain) -> None:
    strategy = CreditSpread(direction="bearish", short_delta=0.16, long_delta=0.08)

    legs = strategy.build_legs(sample_chain)

    assert len(legs) == 2
    assert all(leg.option_type == "CE" for leg in legs)
    by_strike = {leg.strike: leg for leg in legs}
    assert by_strike[120].side == "SELL"
    assert by_strike[130].side == "BUY"

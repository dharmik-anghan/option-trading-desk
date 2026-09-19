from __future__ import annotations

from broker.models import OptionChain
from strategies.iron_condor import IronCondor


def test_iron_condor_sells_short_delta_buys_long_delta_wings(sample_chain: OptionChain) -> None:
    strategy = IronCondor(short_delta=0.16, long_delta=0.08, quantity=1)

    legs = strategy.build_legs(sample_chain)

    assert len(legs) == 4
    by_strike = {leg.strike: leg for leg in legs}

    assert by_strike[120].option_type == "CE"
    assert by_strike[120].side == "SELL"
    assert by_strike[130].option_type == "CE"
    assert by_strike[130].side == "BUY"
    assert by_strike[80].option_type == "PE"
    assert by_strike[80].side == "SELL"
    assert by_strike[70].option_type == "PE"
    assert by_strike[70].side == "BUY"


def test_iron_condor_quantity_applies_to_all_legs(sample_chain: OptionChain) -> None:
    strategy = IronCondor(short_delta=0.16, long_delta=0.08, quantity=3)

    legs = strategy.build_legs(sample_chain)

    assert all(leg.quantity == 3 for leg in legs)

from __future__ import annotations

from broker.models import OptionChain
from strategies.short_strangle import ShortStrangle


def test_short_strangle_sells_both_sides_at_target_delta(sample_chain: OptionChain) -> None:
    strategy = ShortStrangle(target_delta=0.16, quantity=2)

    legs = strategy.build_legs(sample_chain)

    assert len(legs) == 2
    call_leg = next(leg for leg in legs if leg.option_type == "CE")
    put_leg = next(leg for leg in legs if leg.option_type == "PE")
    assert call_leg.strike == 120
    assert call_leg.side == "SELL"
    assert call_leg.premium == 12.0
    assert call_leg.quantity == 2
    assert put_leg.strike == 80
    assert put_leg.side == "SELL"
    assert put_leg.quantity == 2


def test_short_strangle_default_quantity_is_one(sample_chain: OptionChain) -> None:
    strategy = ShortStrangle(target_delta=0.16)

    legs = strategy.build_legs(sample_chain)

    assert all(leg.quantity == 1 for leg in legs)

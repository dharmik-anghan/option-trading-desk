from __future__ import annotations

import pytest

from broker.models import OptionChain
from strategies.selection import select_atm, select_by_delta


def test_select_by_delta_finds_exact_match(sample_chain: OptionChain) -> None:
    row = select_by_delta(sample_chain, "CE", target_delta=0.16)
    assert row.strike == 120


def test_select_by_delta_matches_put_by_absolute_value(sample_chain: OptionChain) -> None:
    row = select_by_delta(sample_chain, "PE", target_delta=0.16)
    assert row.strike == 80


def test_select_by_delta_picks_closest_when_no_exact_match(sample_chain: OptionChain) -> None:
    # No CE row has delta 0.20 exactly; 0.16 (diff 0.04) is closer than 0.30 (diff 0.10).
    row = select_by_delta(sample_chain, "CE", target_delta=0.20)
    assert row.strike == 120


def test_select_by_delta_raises_when_no_rows_for_type(sample_chain: OptionChain) -> None:
    only_calls = sample_chain.model_copy(
        update={"rows": [r for r in sample_chain.rows if r.option_type == "CE"]}
    )
    with pytest.raises(ValueError, match="PE"):
        select_by_delta(only_calls, "PE", target_delta=0.16)


def test_select_atm_finds_strike_closest_to_underlying(sample_chain: OptionChain) -> None:
    row = select_atm(sample_chain, "CE")
    assert row.strike == 100

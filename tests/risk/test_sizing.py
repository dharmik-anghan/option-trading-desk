from __future__ import annotations

import math

import pytest

from risk.sizing import max_quantity_for_risk


def test_caps_quantity_by_max_risk_amount() -> None:
    # Willing to risk 2% of 100000 = 2000. Each lot loses 500 max -> 4 lots.
    qty = max_quantity_for_risk(capital=100000, max_risk_pct=2.0, max_loss_per_lot=500)

    assert qty == 4


def test_rounds_down_to_whole_lots() -> None:
    # Risk budget 2000, loss per lot 700 -> 2 lots (not 2.857).
    qty = max_quantity_for_risk(capital=100000, max_risk_pct=2.0, max_loss_per_lot=700)

    assert qty == 2


def test_returns_zero_when_single_lot_exceeds_budget() -> None:
    qty = max_quantity_for_risk(capital=100000, max_risk_pct=1.0, max_loss_per_lot=5000)

    assert qty == 0


def test_unbounded_max_loss_per_lot_returns_zero() -> None:
    # A naked strategy's max_loss is -inf; sizing can't be based on it at all.
    qty = max_quantity_for_risk(capital=100000, max_risk_pct=2.0, max_loss_per_lot=math.inf)

    assert qty == 0


def test_rejects_non_positive_max_loss_per_lot() -> None:
    with pytest.raises(ValueError):
        max_quantity_for_risk(capital=100000, max_risk_pct=2.0, max_loss_per_lot=0)

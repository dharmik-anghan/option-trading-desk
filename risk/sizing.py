"""Position sizing: how many lots can be traded within a risk budget.

`max_loss_per_lot` is expected to be a positive number (the loss magnitude
for one lot, e.g. `-analytics.payoff.PayoffResult.max_loss` scaled to one
lot). A strategy with unbounded risk (max_loss == -math.inf, e.g. a naked
short strangle) can't be sized this way at all — no finite quantity makes an
infinite loss "within budget" — so it returns 0 rather than a misleading
finite number.
"""

from __future__ import annotations

import math


def max_quantity_for_risk(capital: float, max_risk_pct: float, max_loss_per_lot: float) -> int:
    if max_loss_per_lot <= 0:
        raise ValueError("max_loss_per_lot must be a positive loss magnitude")
    if math.isinf(max_loss_per_lot):
        return 0

    risk_budget = capital * (max_risk_pct / 100)
    return int(risk_budget // max_loss_per_lot)

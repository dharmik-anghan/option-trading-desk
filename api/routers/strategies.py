"""Strategy review: what a structure would look like if placed now.

Never places anything. The same pre-trade checks this returns are re-run and
enforced by the orders router, so a client cannot skip them.
"""

from __future__ import annotations

import math

from fastapi import APIRouter, HTTPException

from analytics.payoff import (
    payoff_curve_points,
)
from api.deps import BrokerDep
from api.pricing import (
    today_curve,
    years_to_expiry,
)
from api.schemas import (
    LegResponse,
    PayoffPoint,
    RiskCheckResponse,
    StrategySignalResponse,
)
from execution.strategy_review import UnknownStrategy, review

router = APIRouter()


@router.get("/api/strategies/{name}", response_model=StrategySignalResponse)
def strategy_signal(
    name: str,
    symbol: str,
    broker: BrokerDep,
    quantity: int = 1,
    expiry: str = "",
) -> StrategySignalResponse:
    try:
        reviewed = review(name, symbol, quantity, broker, expiry)
    except UnknownStrategy:
        raise HTTPException(status_code=404, detail=f"Unknown strategy '{name}'") from None
    chain, legs, result, pre_trade = (
        reviewed.chain, reviewed.legs, reviewed.payoff, reviewed.pre_trade
    )
    years = years_to_expiry(chain)

    return StrategySignalResponse(
        strategy=name,
        symbol=symbol,
        underlying_ltp=chain.underlying_ltp,
        legs=[LegResponse(**vars(leg)) for leg in legs],
        max_profit=None if math.isinf(result.max_profit) else result.max_profit,
        max_loss=None if math.isinf(result.max_loss) else result.max_loss,
        breakevens=result.breakevens,
        payoff_curve=[
            PayoffPoint(spot=x, payoff=value)
            for x, value in payoff_curve_points(result, chain.underlying_ltp)
        ],
        payoff_curve_today=today_curve(result, chain),
        days_to_expiry=None if years is None else round(years * 365.0, 2),
        pre_trade_checks=[
            RiskCheckResponse(passed=c.passed, reason=c.reason) for c in pre_trade.checks
        ],
        can_place=pre_trade.passed,
    )

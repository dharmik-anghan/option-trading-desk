"""Strategy review: what a structure would look like if placed now.

Never places anything. The same pre-trade checks this returns are re-run and
enforced by the orders router, so a client cannot skip them.
"""

from __future__ import annotations

import math

from fastapi import APIRouter, HTTPException

from analytics.payoff import (
    Leg,
    PayoffResult,
    analyze,
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
from broker.base import OptionsBroker
from broker.models import OptionChain
from risk.pre_trade_check import (
    DEFAULT_MAX_LOSS_LIMIT,
    DEFAULT_MAX_RISK_PCT,
    DEFAULT_REQUIRED_MARGIN_PLACEHOLDER,
    PreTradeCheckResult,
    run_pre_trade_checks,
)
from strategies.base import Strategy
from strategies.credit_spread import CreditSpread
from strategies.iron_condor import IronCondor
from strategies.short_strangle import ShortStrangle

router = APIRouter()


_STRATEGY_STRIKE_COUNT = 40

def _strategies() -> dict[str, Strategy]:
    # Built fresh per request rather than module-level, so each request
    # gets its own strategy instance (they're mutable dataclasses).
    return {
        "short_strangle": ShortStrangle(),
        "iron_condor": IronCondor(),
        "credit_spread_bullish": CreditSpread(direction="bullish"),
        "credit_spread_bearish": CreditSpread(direction="bearish"),
    }

def _evaluate(
    name: str, symbol: str, quantity: int, broker: OptionsBroker, expiry_token: str = ""
) -> tuple[OptionChain, list[Leg], PayoffResult, PreTradeCheckResult]:
    strategy = _strategies().get(name)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"Unknown strategy '{name}'")
    strategy.quantity = quantity  # type: ignore[attr-defined]

    # 40 either side, not 15: a 0.08-delta wing on a monthly expiry sits well
    # outside a +/-3% window, and clamping it to the window edge is what made
    # the short and long legs land on the same strike.
    chain = broker.get_option_chain(
        symbol, strike_count=_STRATEGY_STRIKE_COUNT, expiry_token=expiry_token
    )
    legs = strategy.build_legs(chain)
    payoff = analyze(legs)

    funds = broker.get_funds()
    pre_trade = run_pre_trade_checks(
        payoff=payoff,
        available_funds=funds.available_balance,
        required_margin=DEFAULT_REQUIRED_MARGIN_PLACEHOLDER,
        capital=funds.total_balance,
        max_risk_pct=DEFAULT_MAX_RISK_PCT,
        max_loss_limit=DEFAULT_MAX_LOSS_LIMIT,
    )
    return chain, legs, payoff, pre_trade

@router.get("/api/strategies/{name}", response_model=StrategySignalResponse)
def strategy_signal(
    name: str,
    symbol: str,
    broker: BrokerDep,
    quantity: int = 1,
    expiry: str = "",
) -> StrategySignalResponse:
    chain, legs, result, pre_trade = _evaluate(name, symbol, quantity, broker, expiry)
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

"""What a strategy's structure would look like if placed now, and whether it may be.

Shared by the review endpoint, which only shows it, and the orders endpoint,
which re-runs it and refuses an order whose checks fail - so a client cannot
skip them.
"""

from __future__ import annotations

from dataclasses import dataclass

from analytics.payoff import Leg, PayoffResult, analyze
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

#: 40 either side, not 15: a 0.08-delta wing on a monthly expiry sits well
#: outside a +/-3% window, and clamping it to the window edge is what made the
#: short and long legs land on the same strike.
STRIKE_COUNT = 40


class UnknownStrategy(LookupError):
    """No strategy by that name."""


def _strategies() -> dict[str, Strategy]:
    # Built fresh per call rather than module-level, so each review gets its own
    # strategy instance (they're mutable dataclasses).
    return {
        "short_strangle": ShortStrangle(),
        "iron_condor": IronCondor(),
        "credit_spread_bullish": CreditSpread(direction="bullish"),
        "credit_spread_bearish": CreditSpread(direction="bearish"),
    }


@dataclass(frozen=True)
class Review:
    chain: OptionChain
    legs: list[Leg]
    payoff: PayoffResult
    pre_trade: PreTradeCheckResult


def review(
    name: str, symbol: str, quantity: int, broker: OptionsBroker, expiry_token: str = ""
) -> Review:
    """Build the named strategy against the live chain and run the pre-trade checks."""
    strategy = _strategies().get(name)
    if strategy is None:
        raise UnknownStrategy(name)
    strategy.quantity = quantity  # type: ignore[attr-defined]

    chain = broker.get_option_chain(symbol, strike_count=STRIKE_COUNT, expiry_token=expiry_token)
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
    return Review(chain, legs, payoff, pre_trade)

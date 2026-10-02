"""Phase 4 checkpoint: run the full risk gate against a real strategy built
from a real live option chain, using real account funds.

Run: uv run python scripts/pre_trade_check.py [SYMBOL] [STRATEGY]
Same STRATEGY choices as scripts/analyze_strategy.py.

`required_margin` and `capital`/`max_risk_pct`/`max_loss_limit` are set to
placeholder values below - Fyers' SDK has no pre-trade margin calculator
(see docs/GLOSSARY.md), so a real required-margin figure has to come from
somewhere else (manual entry, or a broker that does expose one) before this
is wired into a real confirm flow in Phase 5.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analytics.payoff import analyze  # noqa: E402
from broker.factory import options_broker  # noqa: E402
from risk.pre_trade_check import (  # noqa: E402
    DEFAULT_MAX_LOSS_LIMIT,
    DEFAULT_MAX_RISK_PCT,
    DEFAULT_REQUIRED_MARGIN_PLACEHOLDER,
    run_pre_trade_checks,
)
from strategies.base import Strategy  # noqa: E402
from strategies.credit_spread import CreditSpread  # noqa: E402
from strategies.iron_condor import IronCondor  # noqa: E402
from strategies.short_strangle import ShortStrangle  # noqa: E402

STRATEGIES: dict[str, Strategy] = {
    "short_strangle": ShortStrangle(),
    "iron_condor": IronCondor(),
    "credit_spread_bullish": CreditSpread(direction="bullish"),
    "credit_spread_bearish": CreditSpread(direction="bearish"),
}


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NSE:NIFTY50-INDEX"
    strategy_name = sys.argv[2] if len(sys.argv) > 2 else "iron_condor"

    strategy = STRATEGIES.get(strategy_name)
    if strategy is None:
        print(f"Unknown strategy '{strategy_name}'. Choose from: {list(STRATEGIES)}")
        return 1

    broker = options_broker()

    funds = broker.get_funds()
    print(f"Available balance: {funds.available_balance} (total {funds.total_balance})")

    chain = broker.get_option_chain(symbol, strike_count=15)
    legs = strategy.build_legs(chain)
    payoff = analyze(legs)
    print(f"{strategy.name}: max profit {payoff.max_profit}, max loss {payoff.max_loss}")

    result = run_pre_trade_checks(
        payoff=payoff,
        available_funds=funds.available_balance,
        required_margin=DEFAULT_REQUIRED_MARGIN_PLACEHOLDER,
        capital=funds.total_balance,
        max_risk_pct=DEFAULT_MAX_RISK_PCT,
        max_loss_limit=DEFAULT_MAX_LOSS_LIMIT,
    )

    print("\nPre-trade checks (required_margin is a placeholder, see docstring):")
    for check in result.checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"  [{status}] {check.reason}")
    print(
        f"Max sizeable quantity within {DEFAULT_MAX_RISK_PCT}% risk budget: {result.max_quantity}"
    )
    print(f"Overall: {'PASS' if result.passed else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

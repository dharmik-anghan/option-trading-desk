"""Phase 5 checkpoint: build a real strategy, run the pre-trade risk gate,
and — only if you type CONFIRM exactly — place real orders via Fyers.

Run: uv run python scripts/place_strategy_order.py [SYMBOL] [STRATEGY] [QUANTITY]
Same STRATEGY choices as scripts/analyze_strategy.py. QUANTITY is lots
(default 1 - keep it small for the checkpoint).

This places REAL orders with REAL money if you confirm. Review the printed
legs/max-loss/breakevens carefully before typing CONFIRM. Nothing is sent to
Fyers unless you type the exact word CONFIRM at the prompt.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analytics.payoff import analyze  # noqa: E402
from broker.fyers import FyersBroker  # noqa: E402
from execution.confirm import confirm_and_place  # noqa: E402
from execution.manager import ExecutionManager  # noqa: E402
from risk.pre_trade_check import run_pre_trade_checks  # noqa: E402
from settings import load_settings  # noqa: E402
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

# Same known-gap placeholder as scripts/pre_trade_check.py - see docs/GLOSSARY.md.
PLACEHOLDER_REQUIRED_MARGIN = 50000.0
MAX_RISK_PCT = 2.0
MAX_LOSS_LIMIT = 5000.0


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NSE:NIFTY50-INDEX"
    strategy_name = sys.argv[2] if len(sys.argv) > 2 else "iron_condor"
    quantity = int(sys.argv[3]) if len(sys.argv) > 3 else 1

    strategy = STRATEGIES.get(strategy_name)
    if strategy is None:
        print(f"Unknown strategy '{strategy_name}'. Choose from: {list(STRATEGIES)}")
        return 1
    strategy.quantity = quantity  # type: ignore[attr-defined]

    settings = load_settings()
    broker = FyersBroker(
        client_id=settings.fyers_client_id, access_token=settings.fyers_access_token
    )
    manager = ExecutionManager(broker)

    funds = broker.get_funds()
    chain = broker.get_option_chain(symbol, strike_count=15)
    legs = strategy.build_legs(chain)
    payoff = analyze(legs)

    pre_trade = run_pre_trade_checks(
        payoff=payoff,
        available_funds=funds.available_balance,
        required_margin=PLACEHOLDER_REQUIRED_MARGIN,
        capital=funds.total_balance,
        max_risk_pct=MAX_RISK_PCT,
        max_loss_limit=MAX_LOSS_LIMIT,
    )
    for check in pre_trade.checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"  [{status}] {check.reason}")

    results = confirm_and_place(legs, payoff, pre_trade, manager)
    if results is None:
        return 1

    print("\nOrders placed:")
    for result in results:
        print(f"  order_id={result.order_id} message={result.message}")

    print("\nReconciling against live positions...")
    positions = broker.get_positions()
    ordered_symbols = {leg.symbol for leg in legs}
    matched = [p for p in positions if p.symbol in ordered_symbols]
    for position in matched:
        print(f"  {position.symbol}: net_qty={position.net_quantity} avg={position.average_price}")
    if len(matched) != len(ordered_symbols):
        print(
            f"  WARNING: expected {len(ordered_symbols)} symbols in positions, "
            f"found {len(matched)}. Orders may still be filling - check again shortly."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

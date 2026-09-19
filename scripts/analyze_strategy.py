"""Phase 3 checkpoint: build a real strategy from a live option chain and
print the numbers a semi-auto confirm screen (Phase 5) will show.

Run: uv run python scripts/analyze_strategy.py [SYMBOL] [STRATEGY]
STRATEGY is one of: short_strangle, iron_condor, credit_spread_bullish,
credit_spread_bearish. Defaults to NSE:NIFTY50-INDEX / iron_condor.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analytics.payoff import analyze  # noqa: E402
from broker.fyers import FyersBroker  # noqa: E402
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


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NSE:NIFTY50-INDEX"
    strategy_name = sys.argv[2] if len(sys.argv) > 2 else "iron_condor"

    strategy = STRATEGIES.get(strategy_name)
    if strategy is None:
        print(f"Unknown strategy '{strategy_name}'. Choose from: {list(STRATEGIES)}")
        return 1

    settings = load_settings()
    broker = FyersBroker(
        client_id=settings.fyers_client_id, access_token=settings.fyers_access_token
    )

    chain = broker.get_option_chain(symbol, strike_count=15)
    legs = strategy.build_legs(chain)
    result = analyze(legs)

    print(f"{strategy.name} on {symbol} (spot {chain.underlying_ltp})")
    for leg in legs:
        print(f"  {leg.side:4} {leg.quantity}x {leg.option_type} {leg.strike} @ {leg.premium}")
    print(f"  Max profit: {result.max_profit}")
    print(f"  Max loss:   {result.max_loss}")
    print(f"  Breakevens: {result.breakevens}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

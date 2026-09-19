"""Strike-selection helpers shared by strategy implementations.

Kept separate from any one strategy so selection logic (by delta, by ATM)
is independently testable and reusable across strategies — an iron condor
and a credit spread both pick strikes "by delta", they just combine the
results differently.
"""

from __future__ import annotations

from broker.models import OptionChain, OptionChainRow, OptionType


def _rows_for_type(chain: OptionChain, option_type: OptionType) -> list[OptionChainRow]:
    rows = [r for r in chain.rows if r.option_type == option_type]
    if not rows:
        raise ValueError(f"Option chain for {chain.underlying_symbol} has no {option_type} rows")
    return rows


def select_by_delta(
    chain: OptionChain, option_type: OptionType, target_delta: float
) -> OptionChainRow:
    """Pick the strike whose |delta| is closest to `target_delta`.

    Requires broker-supplied Greeks on the rows (Fyers provides these via
    the `greeks=1` param — see `broker/fyers.py`).
    """
    candidates = [r for r in _rows_for_type(chain, option_type) if r.greeks is not None]
    if not candidates:
        raise ValueError(
            f"No {option_type} rows with Greeks data in chain for {chain.underlying_symbol}"
        )
    return min(candidates, key=lambda r: abs(abs(r.greeks.delta) - target_delta))  # type: ignore[union-attr]


def select_atm(chain: OptionChain, option_type: OptionType) -> OptionChainRow:
    """Pick the strike closest to the underlying's current price."""
    candidates = _rows_for_type(chain, option_type)
    return min(candidates, key=lambda r: abs(r.strike - chain.underlying_ltp))

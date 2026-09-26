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


def is_quoted(row: OptionChainRow) -> bool:
    """Whether this strike has a market worth selecting.

    Deep wings come back from the broker with `iv: 0` and a zero price -
    that is "no quote", not "a free option". Selecting one produces a leg
    that cannot actually be traded at the price shown.
    """
    if row.greeks is None or row.greeks.iv <= 0:
        return False
    return row.ltp > 0


def select_by_delta(
    chain: OptionChain,
    option_type: OptionType,
    target_delta: float,
    *,
    exclude_strikes: frozenset[float] = frozenset(),
) -> OptionChainRow:
    """Pick the quoted strike whose |delta| is closest to `target_delta`.

    Requires broker-supplied Greeks on the rows (Fyers provides these via
    the `greeks=1` param - see `broker/fyers.py`).

    `exclude_strikes` keeps a multi-leg strategy from picking the same strike
    twice. Without it, a target delta that lies outside the loaded window
    makes every target resolve to the same outermost strike, and a four-leg
    structure collapses into two offsetting pairs worth exactly nothing -
    while still costing four real orders to place.
    """
    candidates = [
        r
        for r in _rows_for_type(chain, option_type)
        if is_quoted(r) and r.strike not in exclude_strikes
    ]
    if not candidates:
        raise ValueError(
            f"No quoted {option_type} strikes left to choose from in the chain for "
            f"{chain.underlying_symbol} - the loaded strike window may be too narrow"
        )
    return min(candidates, key=lambda r: abs(abs(r.greeks.delta) - target_delta))  # type: ignore[union-attr]


def select_atm(chain: OptionChain, option_type: OptionType) -> OptionChainRow:
    """Pick the strike closest to the underlying's current price."""
    candidates = _rows_for_type(chain, option_type)
    return min(candidates, key=lambda r: abs(r.strike - chain.underlying_ltp))

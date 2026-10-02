"""Reading a venue's contract symbols.

A fill or a stored leg arrives with nothing but its symbol, and a symbol's
spelling is the venue's own - Fyers writes "NSE:NIFTY26OCT23100CE", another
broker does not. So the spelling is read by a codec each adapter package
supplies (Fyers' is `broker/fyers/symbols.py`), handed out per venue by
`broker/factory.py`. Code above `broker/` asks the codec rather than parsing.
"""

from __future__ import annotations

from typing import Protocol

from broker.models import Expiry, OptionType


class ContractCodec(Protocol):
    def parse_contract(self, symbol: str) -> tuple[str, float, OptionType] | None:
        """An option symbol's series, strike and type, or None if it is not one.

        The series names the underlying and the expiry together: two legs with
        the same series are in the same expiry.
        """
        ...

    def series_prefix(self, symbol: str, strike: float) -> str | None:
        """The series part of a contract symbol, given its strike."""
        ...

    def expiry_for_symbol(self, symbol: str, expiries: list[Expiry]) -> Expiry | None:
        """Which of the listed expiries `symbol` belongs to, or None."""
        ...

    def futures_symbol(self, option_symbol: str, strike: float) -> str | None:
        """The futures contract on the same underlying and expiry as an option."""
        ...


def common_expiry(
    codec: ContractCodec, symbols: list[str], expiries: list[Expiry]
) -> Expiry | None:
    """The single expiry every symbol shares, or None.

    None when the symbols straddle expiries - a calendar spread has no one
    time-to-expiry, so a single pre-expiry curve would be a fiction.
    """
    if not symbols:
        return None
    matched = [codec.expiry_for_symbol(s, expiries) for s in symbols]
    if any(m is None for m in matched):
        return None
    tokens = {m.token for m in matched if m is not None}
    if len(tokens) != 1:
        return None
    return matched[0]

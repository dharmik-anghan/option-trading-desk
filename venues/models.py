"""What one venue is."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from venues.calendar import Session


class AssetClass(StrEnum):
    """What kind of thing a venue lists.

    This drives which panels a desk shows, because the two share almost no
    vocabulary: index options have strikes, expiries, greeks and a payoff at
    expiry, while perpetuals have leverage, funding and a liquidation price and
    never expire. Kept as the asset class rather than the venue id so a second
    options broker or a second perps venue slots in without new branches.
    """

    INDEX_OPTIONS = "index_options"
    PERPETUALS = "perpetuals"


class Capability(StrEnum):
    """One thing a venue's adapter can do.

    Mirrors the protocols in `broker/base.py`. Declared on the venue so the API
    can tell a client what to show without constructing an adapter (which needs
    credentials); `tests/venues/test_registry.py` asserts the declaration
    matches what the adapter actually implements, so it cannot quietly rot.
    """

    QUOTES = "quotes"
    HISTORY = "history"
    TRADING = "trading"
    OPTION_CHAIN = "option_chain"
    STREAMING = "streaming"


@dataclass(frozen=True)
class VenueSpec:
    """A venue, described. No connection, no credentials, no I/O."""

    id: str
    #: Shown in the venue switcher.
    name: str
    asset_class: AssetClass
    #: What P&L and balances are denominated in. Mixing two currencies in one
    #: total is meaningless, so a desk shows one venue at a time.
    quote_currency: str
    #: When it trades.
    session: Session
    capabilities: frozenset[Capability]

    def can(self, capability: Capability) -> bool:
        return capability in self.capabilities

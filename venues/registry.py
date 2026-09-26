"""The catalogue of venues the desk knows about.

One entry per venue. Adding a venue is adding an entry here plus an adapter in
`broker/` and a factory line in `api/dependencies.py` - nothing above this
package needs to change, which is the whole point of it existing.
"""

from __future__ import annotations

from venues.calendar import Session
from venues.models import AssetClass, Capability, VenueSpec

FYERS = VenueSpec(
    id="fyers",
    name="NSE index options",
    asset_class=AssetClass.INDEX_OPTIONS,
    quote_currency="INR",
    session=Session.NSE_FO,
    capabilities=frozenset(
        {
            Capability.QUOTES,
            Capability.HISTORY,
            Capability.TRADING,
            Capability.OPTION_CHAIN,
            # Declared because the protocol is implemented. It raises
            # NotImplementedError today - see broker/fyers.py - so a client
            # should treat this as "the adapter has the method", not "ticks
            # work". When streaming lands this comment goes away rather than
            # the capability appearing.
            Capability.STREAMING,
        }
    ),
)

#: Insertion order is the order the switcher shows them in.
VENUES: dict[str, VenueSpec] = {FYERS.id: FYERS}

#: The venue used when a request does not name one. Every endpoint that existed
#: before venues did keeps working unchanged because of this.
DEFAULT_VENUE_ID = FYERS.id


class UnknownVenueError(KeyError):
    """Asked for a venue that is not in the catalogue."""


def get(venue_id: str | None = None) -> VenueSpec:
    """Look up a venue, falling back to the default when not named."""
    wanted = venue_id or DEFAULT_VENUE_ID
    try:
        return VENUES[wanted]
    except KeyError:
        known = ", ".join(VENUES)
        raise UnknownVenueError(f"unknown venue {wanted!r}; known venues: {known}") from None


def listed() -> list[VenueSpec]:
    return list(VENUES.values())

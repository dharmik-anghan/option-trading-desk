"""Building an adapter for a venue.

The one module that knows every adapter by name. Everything above it asks for
a venue - or for the venue serving an asset class - and gets back a protocol,
so adding a broker is an adapter, a catalogue entry in `venues/registry.py`
and a line in `FACTORIES` here.

Kept out of `venues/` so that importing the catalogue never needs a credential.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from broker.base import Broker, OptionsBroker, OptionsData
from broker.cache import CachedBroker
from broker.fyers import FyersBroker
from broker.shark import SharkBroker
from broker.token_store import get_access_token
from settings import load_settings
from venues import AssetClass, VenueSpec, serving
from venues import get as get_venue


def _build_fyers() -> Broker:
    settings = load_settings()
    return FyersBroker(client_id=settings.fyers_client_id, access_token=get_access_token(settings))


def _build_shark() -> Broker:
    settings = load_settings()
    return SharkBroker(api_key=settings.shark_api_key, api_secret=settings.shark_api_secret)


@dataclass(frozen=True)
class Factory:
    build: Callable[[], Broker]
    #: Serve repeated reads from a short-lived cache - for a venue whose rate
    #: limit four polling panels would breach. See `broker/cache.py`.
    cached: bool = False


#: How to build an adapter for each venue in the catalogue. A venue in the
#: registry with no factory here is a configuration error, and
#: `tests/api/test_venues.py` checks the two lists agree.
FACTORIES: dict[str, Factory] = {
    "fyers": Factory(_build_fyers, cached=True),
    "shark": Factory(_build_shark),
}

# One cache per venue for the whole process, so it is shared by every request
# rather than rebuilt per request - which would cache nothing at all. The rate
# limit being protected against is per account, not per desk.
_caches: dict[str, CachedBroker] = {}


def broker_for(venue: VenueSpec | None = None) -> Broker:
    """A broker for one venue, the default venue when none is named.

    Built per call rather than once at import, so an expired token is refreshed
    mid-session instead of poisoning every later call. A cached venue's adapter
    is handed to its long-lived cache, which keeps what it already holds: the
    values belong to the same account either way.
    """
    spec = venue or get_venue()
    try:
        factory = FACTORIES[spec.id]
    except KeyError:
        raise NotImplementedError(
            f"venue {spec.id!r} is in the catalogue but has no adapter factory"
        ) from None
    adapter = factory.build()
    if not factory.cached:
        return adapter
    inner = cast(OptionsBroker, adapter)
    cache = _caches.get(spec.id)
    if cache is None:
        cache = _caches[spec.id] = CachedBroker(inner)
    else:
        cache.rebind(inner)
    return cache


def options_broker(venue: VenueSpec | None = None) -> OptionsBroker:
    """The broker for an options desk - the named venue, or the one serving index options."""
    spec = venue or serving(AssetClass.INDEX_OPTIONS)
    broker = broker_for(spec)
    if not isinstance(broker, OptionsData):
        raise NotImplementedError(f"venue {spec.id!r} does not list option chains")
    return cast(OptionsBroker, broker)

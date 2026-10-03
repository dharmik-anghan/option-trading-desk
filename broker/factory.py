"""Building an adapter for a venue.

The one module that knows every adapter by name. Everything above it asks for
a venue - or for the venue serving an asset class - and gets back a protocol,
so adding a broker is an adapter package (the broker, and a codec for its
contract symbols if it lists options), a catalogue entry in
`venues/registry.py`, and a line in `FACTORIES` here.

Kept out of `venues/` so that importing the catalogue never needs a credential.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from broker.base import AsyncStreaming, Broker, OptionsBroker, OptionsData
from broker.cache import CachedBroker
from broker.contracts import ContractCodec
from broker.errors import AuthFailed
from broker.fyers import SYMBOLS as FYERS_SYMBOLS
from broker.fyers import FyersBroker
from broker.fyers.stream import FyersStream
from broker.fyers.token_store import get_access_token
from broker.shark import SharkBroker
from broker.shark.stream import SharkStream
from settings import Settings, load_settings
from venues import AssetClass, VenueSpec, serving
from venues import get as get_venue


def _build_fyers() -> Broker:
    settings = load_settings()
    if not settings.has_fyers:
        raise AuthFailed("Fyers is not configured - set FYERS_CLIENT_ID and friends in .env")
    return FyersBroker(client_id=settings.fyers_client_id, access_token=get_access_token(settings))


def _build_shark() -> Broker:
    settings = load_settings()
    if not settings.has_shark:
        raise AuthFailed("Shark is not configured - set SHARK_API_KEY and SHARK_API_SECRET")
    return SharkBroker(api_key=settings.shark_api_key, api_secret=settings.shark_api_secret)


@dataclass(frozen=True)
class Factory:
    build: Callable[[], Broker]
    #: Serve repeated reads from a short-lived cache - for a venue whose rate
    #: limit four polling panels would breach. See `broker/cache.py`.
    cached: bool = False
    #: How this venue spells a contract. Options venues only.
    codec: ContractCodec | None = None
    #: Whether the settings hold this venue's credentials. An unconfigured venue
    #: is skipped by the app's background work rather than retried and failed.
    configured: Callable[[Settings], bool] = lambda _: True
    #: The venue's pushed prices, for a venue that pushes them.
    stream: Callable[[], AsyncStreaming] | None = None


#: How to build an adapter for each venue in the catalogue. A venue in the
#: registry with no factory here is a configuration error, and
#: `tests/api/test_venues.py` checks the two lists agree.
FACTORIES: dict[str, Factory] = {
    "fyers": Factory(
        _build_fyers,
        cached=True,
        codec=FYERS_SYMBOLS,
        configured=lambda s: s.has_fyers,
        stream=FyersStream,
    ),
    "shark": Factory(_build_shark, configured=lambda s: s.has_shark, stream=SharkStream),
}

# One cache per venue for the whole process, so it is shared by every request
# rather than rebuilt per request - which would cache nothing at all. The rate
# limit being protected against is per account, not per desk.
_caches: dict[str, CachedBroker] = {}


def _factory(spec: VenueSpec) -> Factory:
    try:
        return FACTORIES[spec.id]
    except KeyError:
        raise NotImplementedError(
            f"venue {spec.id!r} is in the catalogue but has no adapter factory"
        ) from None


def broker_for(venue: VenueSpec | None = None) -> Broker:
    """A broker for one venue, the default venue when none is named.

    Built per call rather than once at import, so an expired token is refreshed
    mid-session instead of poisoning every later call. A cached venue's adapter
    is handed to its long-lived cache, which keeps what it already holds: the
    values belong to the same account either way.
    """
    spec = venue or get_venue()
    factory = _factory(spec)
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


def codec_for(venue: VenueSpec | None = None) -> ContractCodec:
    """How a venue spells its contracts - the named one, or the one serving index options."""
    spec = venue or serving(AssetClass.INDEX_OPTIONS)
    codec = _factory(spec).codec
    if codec is None:
        raise NotImplementedError(f"venue {spec.id!r} has no contract symbols to read")
    return codec


def is_configured(venue: VenueSpec, settings: Settings | None = None) -> bool:
    """Whether this venue's credentials are present."""
    return _factory(venue).configured(settings or load_settings())


def stream_for(venue: VenueSpec) -> AsyncStreaming | None:
    """A new tick stream for the venue, or None if it does not push prices."""
    open_stream = _factory(venue).stream
    return open_stream() if open_stream else None


def invalidate_account(venue: VenueSpec) -> None:
    """Drop a cached venue's account reads, so the next one goes to the broker."""
    cache = _caches.get(venue.id)
    if cache is not None:
        cache.invalidate_account()

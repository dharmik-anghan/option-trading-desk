"""FastAPI dependency providers.

Kept as thin functions (not constructed at import time) so tests can
override them via `app.dependency_overrides` with a `FakeBroker`/temp DB
path, without ever needing real Fyers credentials or touching the real
`data/trading.db` file.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from broker.base import Broker, OptionsBroker
from broker.cache import CachedBroker
from broker.fyers import FyersBroker
from broker.shark import SharkBroker
from broker.token_store import get_access_token
from feeds.fetch import Feeds
from feeds.holidays import Holidays
from settings import load_settings
from venues import VenueSpec
from venues import get as get_venue

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "trading.db"


# One cache for the whole process, so it is shared by every request rather
# than rebuilt per request - which would cache nothing at all. The wrapped
# broker inside it is replaced whenever the token changes; the cached reads
# outlive that, which is fine because they are the same account either way.
_cache: CachedBroker | None = None


def _build_fyers() -> OptionsBroker:
    settings = load_settings()
    return FyersBroker(
        client_id=settings.fyers_client_id, access_token=get_access_token(settings)
    )


def _build_shark() -> Broker:
    settings = load_settings()
    return SharkBroker(api_key=settings.shark_api_key, api_secret=settings.shark_api_secret)


#: How to build an adapter for each venue in the catalogue. Kept here rather
#: than in `venues/` so that importing the catalogue never needs a credential.
#: A venue in the registry with no factory here is a configuration error, and
#: `tests/api/test_venues.py` checks the two lists agree.
BROKER_FACTORIES: dict[str, Callable[[], Broker]] = {
    "fyers": _build_fyers,
    "shark": _build_shark,
}


def broker_for(venue: VenueSpec | None = None) -> Broker:
    """A broker for one venue.

    Every venue gets its own process-wide cache, because the rate limit being
    protected against is per account, not per desk.
    """
    spec = venue or get_venue()
    try:
        build = BROKER_FACTORIES[spec.id]
    except KeyError:
        raise NotImplementedError(
            f"venue {spec.id!r} is in the catalogue but has no adapter factory"
        ) from None
    if spec.id == "fyers":
        # The options desk has a cache in front of it; everything else gets the
        # adapter directly until it needs one. A perps venue on a 60-per-minute
        # budget will want its own, but caching chains it does not have would be
        # the wrong wrapper.
        return get_broker()
    return build()


def get_broker() -> OptionsBroker:
    """The options broker, cached.

    Constructed per request (not once at import) so an expired token gets
    refreshed mid-session instead of poisoning every later call with
    `-16 Could not authenticate the user`.

    Handed to a process-wide `CachedBroker`, which holds reads for a few seconds.
    Without it, four panels polling together breach Fyers' ten-per-second cap in
    bursts and the desk goes stale behind a run of 429s - see `broker/cache.py`.

    Typed concretely rather than as `Broker` because the options endpoints need
    chains, and `broker_for` cannot promise those for an arbitrary venue.
    """
    global _cache
    fyers = _build_fyers()
    if _cache is None:
        _cache = CachedBroker(fyers)
    else:
        _cache.rebind(fyers)
    return _cache


def get_db_path() -> Path:
    """Where the database lives.

    Honours DB_PATH, because `scripts/backup_db.py` already does and the two
    disagreeing is a trap: pointing DB_PATH at a copy looked like it worked while
    the app carried on writing to the real file.
    """
    return Path(os.environ.get("DB_PATH", DEFAULT_DB_PATH))


# One set of feeds for the process, so the calendar is fetched a few times a
# day rather than once per request. Holds its own cache - see feeds/fetch.py.
_feeds = Feeds()


def get_feeds() -> Feeds:
    return _feeds


# The exchange's holiday list, fetched once a day. Shared so the session check
# does not go to the network on every request.
_holidays = Holidays()


def get_holidays() -> Holidays:
    return _holidays

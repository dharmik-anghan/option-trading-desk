"""FastAPI dependency providers.

Kept as thin functions (not constructed at import time) so tests can
override them via `app.dependency_overrides` with a `FakeBroker`/temp DB
path, without ever needing real Fyers credentials or touching the real
`data/trading.db` file.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from broker.base import Broker, OptionsBroker
from broker.cache import CachedBroker
from broker.fyers import FyersBroker
from broker.token_store import get_access_token
from feeds.fetch import Feeds
from feeds.holidays import Holidays
from settings import load_settings
from venues import VenueSpec
from venues import get as get_venue
from venues.models import Capability

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


#: How to build an adapter for each venue in the catalogue. Kept here rather
#: than in `venues/` so that importing the catalogue never needs a credential.
#: A venue in the registry with no factory here is a configuration error, and
#: `tests/api/test_venues.py` checks the two lists agree.
BROKER_FACTORIES: dict[str, Callable[[], OptionsBroker]] = {"fyers": _build_fyers}


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
    return _cached(spec, build)


def _cached(spec: VenueSpec, build: Callable[[], OptionsBroker]) -> OptionsBroker:
    global _cache
    inner = build()
    if not spec.can(Capability.OPTION_CHAIN):
        # CachedBroker caches chains, so it only fits an options venue. A perps
        # venue gets its own caching wrapper when one exists; until then it
        # would be wrong to pretend this one applies.
        return inner
    if _cache is None:
        _cache = CachedBroker(inner)
    else:
        _cache.rebind(inner)
    return _cache


def get_broker() -> OptionsBroker:
    """A broker bound to a token that is checked for expiry on every request.

    The Fyers adapter is constructed per request (not once at import) so that
    an expired token gets refreshed mid-session instead of poisoning every
    later call with `-16 Could not authenticate the user`.

    It is handed to a process-wide `CachedBroker`, which holds reads for a few
    seconds. Without it, four panels polling together breach Fyers' 10-per-
    second cap in bursts and the desk goes stale behind a run of 429s - see
    `broker/cache.py`.
    """
    return _cached(get_venue("fyers"), _build_fyers)


def get_db_path() -> Path:
    return DEFAULT_DB_PATH


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

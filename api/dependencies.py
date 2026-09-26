"""FastAPI dependency providers.

Kept as thin functions (not constructed at import time) so tests can
override them via `app.dependency_overrides` with a `FakeBroker`/temp DB
path, without ever needing real Fyers credentials or touching the real
`data/trading.db` file.
"""

from __future__ import annotations

from pathlib import Path

from broker.base import Broker
from broker.cache import CachedBroker
from broker.fyers import FyersBroker
from broker.token_store import get_access_token
from feeds.fetch import Feeds
from feeds.holidays import Holidays
from settings import load_settings

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "trading.db"


# One cache for the whole process, so it is shared by every request rather
# than rebuilt per request - which would cache nothing at all. The wrapped
# broker inside it is replaced whenever the token changes; the cached reads
# outlive that, which is fine because they are the same account either way.
_cache: CachedBroker | None = None


def get_broker() -> Broker:
    """A broker bound to a token that is checked for expiry on every request.

    The Fyers adapter is constructed per request (not once at import) so that
    an expired token gets refreshed mid-session instead of poisoning every
    later call with `-16 Could not authenticate the user`.

    It is handed to a process-wide `CachedBroker`, which holds reads for a few
    seconds. Without it, four panels polling together breach Fyers' 10-per-
    second cap in bursts and the desk goes stale behind a run of 429s - see
    `broker/cache.py`.
    """
    global _cache
    settings = load_settings()
    fyers = FyersBroker(
        client_id=settings.fyers_client_id, access_token=get_access_token(settings)
    )
    if _cache is None:
        _cache = CachedBroker(fyers)
    else:
        _cache.rebind(fyers)
    return _cache


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

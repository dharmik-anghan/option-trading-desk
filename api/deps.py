"""FastAPI dependencies: the providers, and the typed annotations routers use.

Held apart from `api/app.py` so a router can import them without importing the
application - which would be a cycle, since `app.py` imports the routers.

Providers are thin functions rather than values built at import, so tests can
override them via `app.dependency_overrides` with a `FakeBroker` or a temporary
database, without real credentials or the real `data/trading.db`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request

import paths
from broker.base import OptionsBroker
from broker.contracts import ContractCodec
from broker.factory import codec_for, options_broker
from feeds.fetch import Feeds
from feeds.holidays import Holidays
from marketdata import BarService, BarStore
from marketdata.holder import BarStoreHolder
from venues import AssetClass, VenueSpec, serving
from venues import get as get_venue
from venues.registry import UnknownVenueError


def options_venue(venue: str = "") -> VenueSpec:
    """Which options venue a request is for: `?venue=` if given, else the default.

    Every options endpoint takes it, so a second options broker is served by the
    same routes, panels and analytics as the first - only the adapter differs.
    """
    try:
        spec = get_venue(venue) if venue else serving(AssetClass.INDEX_OPTIONS)
    except UnknownVenueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    if spec.asset_class is not AssetClass.INDEX_OPTIONS:
        raise HTTPException(status_code=400, detail=f"{spec.id} does not list options")
    return spec


OptionsVenueDep = Annotated[VenueSpec, Depends(options_venue)]


def get_broker(venue: OptionsVenueDep) -> OptionsBroker:
    """The options broker for this request's venue. See `broker/factory.py`."""
    return options_broker(venue)


def get_codec(venue: OptionsVenueDep) -> ContractCodec:
    """How this request's venue spells its contracts. See `broker/contracts.py`."""
    return codec_for(venue)


def get_db_path() -> Path:
    """Where the database lives. Honours `DB_PATH`, like every script does."""
    return paths.db_path()


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


BrokerDep = Annotated[OptionsBroker, Depends(get_broker)]
CodecDep = Annotated[ContractCodec, Depends(get_codec)]
FeedsDep = Annotated[Feeds, Depends(get_feeds)]
HolidaysDep = Annotated[Holidays, Depends(get_holidays)]
DbPathDep = Annotated[Path, Depends(get_db_path)]


def bar_service(request: Request) -> BarService | None:
    """The bar store's service, or None while the file is held elsewhere.

    One place, because four routers wanted it and each had grown its own
    `getattr` against app state. The holder retries on demand - see
    `marketdata/holder.py` - so a desk that started beside a finishing backfill
    recovers on its own rather than refusing history for the rest of the day.

    A service wired directly onto app state wins over the holder. That is how
    the tests supply one, and an explicit override losing to the real thing is
    the wrong way round - it would have them reading whatever is on this
    machine's disk.
    """
    direct = getattr(request.app.state, "bar_service", None)
    if isinstance(direct, BarService):
        return direct
    holder = getattr(request.app.state, "bars", None)
    return holder.service() if isinstance(holder, BarStoreHolder) else None


def bar_store(request: Request) -> BarStore | None:
    """The store itself, for the few reads that are not bars - funding rates."""
    direct = getattr(request.app.state, "bar_store", None)
    if isinstance(direct, BarStore):
        return direct
    holder = getattr(request.app.state, "bars", None)
    return holder.store if isinstance(holder, BarStoreHolder) else None

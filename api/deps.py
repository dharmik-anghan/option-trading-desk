"""Typed dependency annotations, shared by every router.

Held apart from `api/app.py` so a router can import them without importing the
application - which would be a cycle, since `app.py` imports the routers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request

from api.dependencies import get_broker, get_db_path, get_feeds, get_holidays
from broker.base import OptionsBroker
from feeds.fetch import Feeds
from feeds.holidays import Holidays
from marketdata import BarService, BarStore
from marketdata.holder import BarStoreHolder

BrokerDep = Annotated[OptionsBroker, Depends(get_broker)]
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

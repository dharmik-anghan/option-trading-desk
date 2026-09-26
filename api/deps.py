"""Typed dependency annotations, shared by every router.

Held apart from `api/app.py` so a router can import them without importing the
application - which would be a cycle, since `app.py` imports the routers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends

from api.dependencies import get_broker, get_db_path, get_feeds, get_holidays
from broker.base import OptionsBroker
from feeds.fetch import Feeds
from feeds.holidays import Holidays

BrokerDep = Annotated[OptionsBroker, Depends(get_broker)]
FeedsDep = Annotated[Feeds, Depends(get_feeds)]
HolidaysDep = Annotated[Holidays, Depends(get_holidays)]
DbPathDep = Annotated[Path, Depends(get_db_path)]

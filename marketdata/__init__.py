"""Historical bars, from wherever they can be had, kept so they are fetched once.

The desk's venues serve short histories - Shark gives a few hundred bars - which
draws a chart and will not show a year. This package fills that in from sources that
have the depth, and keeps what it downloads.

Keeping it is not an optimisation. Yahoo refuses after roughly ten requests in two
minutes, and its intraday windows are finite: a one-minute bar older than seven days
cannot be fetched again at any price. Bars not stored when they were available are
gone.

    models.py    a bar, and what identifies a series of them
    store.py     DuckDB on disk. The only module that knows which engine
    yahoo.py     gold and oil. An undocumented endpoint, with a handshake
    binance.py   crypto. A documented API with a published budget
    service.py   read-through: serve from the store, ask a source when due

Nothing here knows about a venue, a desk or an option, which is the point: the same
store holds bars for anything, keyed by the source they came from so that two
sources disagreeing about gold stays two series rather than becoming one wrong one.
"""

from marketdata.models import Bar, Interval, Series
from marketdata.service import BarService, BarsResult
from marketdata.store import BarStore

__all__ = ["Bar", "BarService", "BarStore", "BarsResult", "Interval", "Series"]

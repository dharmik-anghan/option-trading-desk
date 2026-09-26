"""Assembling what the alert watcher judges.

The seam between the watcher (which knows nothing about brokers or HTTP) and the
rest of the app. It reads exactly what the baskets endpoint reads, through the
same pricing helpers, so the desk and the watcher cannot come to disagree about
what a position is worth - one of them warning about a worst case the other does
not show would be worse than no warning.

Blocking on purpose: broker calls and SQLite are synchronous, and the watcher
runs this on a worker thread rather than pretending otherwise.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alerting.watcher import Inputs
from api.pricing import basket_live_curve
from api.store import open_db
from broker.base import OptionsBroker
from broker.models import OptionChain
from execution.basket_status import get_basket_payoff
from execution.portfolio_status import get_portfolio_status
from feeds.fetch import Feeds
from storage.basket_repo import list_baskets as repo_list_baskets

log = logging.getLogger(__name__)


def gather(
    db_path: Path,
    broker: OptionsBroker,
    feeds: Feeds,
) -> Inputs:
    """One pass's worth of data, with partial failure preferred over none.

    The P&L and the structures are fetched separately: if the broker refuses one
    the other is still worth judging, because an expiry warning does not need a
    live price. What is *not* done is passing an absent source off as an empty
    one - `events_loaded` carries that distinction, since the two mean opposite
    things to the engine.
    """
    # Imported here rather than at module scope: the baskets router imports this
    # module's neighbours, and a top-level import would close the circle.
    from api.routers.baskets import _basket_to_response, _rows_for_basket, _spot_for

    total_pnl: float | None = None
    try:
        status = get_portfolio_status(broker)
        total_pnl = status.total_pnl
    except Exception:  # noqa: BLE001 - one unavailable source must not stop the pass
        log.warning("alert pass could not read the portfolio", exc_info=True)

    views = []
    conn = open_db(db_path)
    try:
        baskets = repo_list_baskets(conn)
    finally:
        conn.close()

    chains: dict[tuple[str, str], OptionChain] = {}
    for basket in baskets:
        payoff = get_basket_payoff(basket)
        try:
            valued = basket_live_curve(basket, payoff, broker, chains)
        except Exception:  # noqa: BLE001 - an unpriced basket is still worth judging
            valued = ([], None, None)
        views.append(
            _basket_to_response(
                basket,
                valued,
                _rows_for_basket(basket, chains),
                _spot_for(basket, chains),
            )
        )

    cached = feeds.events()
    # Answered at all? A calendar that has never been fetched, or whose last
    # fetch failed, cannot be judged - see Inputs.events_loaded.
    events_loaded = cached.fetched_at is not None and cached.error is None

    return Inputs(
        total_pnl=total_pnl,
        baskets=views,
        events=cached.events,
        events_loaded=events_loaded,
    )
